from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import socket

from app.core.config import get_settings
from app.services.automation import AutomationService
from app.services.glpi import GLPIClient
from app.services.job_queue import JobQueueService
from app.services.operational_store import OperationalStateStore


class AutomationWorker:
    def __init__(
        self,
        *,
        operational_store: OperationalStateStore,
        job_queue: JobQueueService,
        automation_service: AutomationService,
        worker_id: str | None = None,
    ) -> None:
        self.operational_store = operational_store
        self.job_queue = job_queue
        self.automation_service = automation_service
        self.worker_id = worker_id or f"automation-worker@{socket.gethostname()}"

    async def run_once(self, *, timeout_seconds: int = 5) -> bool:
        retry_job = await self.operational_store.acquire_due_retry_job(worker_id=self.worker_id)
        if retry_job is not None:
            return await self._execute_acquired_job(retry_job)

        queue_item = await self.job_queue.dequeue_job(timeout_seconds=timeout_seconds)
        if queue_item is None:
            return False

        job = await self.operational_store.acquire_job_for_execution(
            queue_item.job_id,
            worker_id=self.worker_id,
            queue_mode=queue_item.queue_mode,
            queue_key=queue_item.queue_key,
        )
        if job is None:
            await self._audit_blocked_queue_item(queue_item.job_id)
            return False

        return await self._execute_acquired_job(job)

    async def _execute_acquired_job(self, job) -> bool:
        request_payload = job.payload_json.get("request")
        parameters = {}
        if isinstance(request_payload, dict):
            raw_parameters = request_payload.get("parameters")
            if isinstance(raw_parameters, dict):
                parameters = raw_parameters

        try:
            result = await self.automation_service.execute(
                automation_name=job.automation_name,
                ticket_id=job.ticket_id,
                parameters=parameters,
            )
        except Exception as exc:
            attempt_count = self.operational_store._extract_attempt_count(job.payload_json)
            max_attempts = self.operational_store._extract_max_attempts(job.payload_json)

            if attempt_count < max_attempts:
                retry_delay_seconds = self._compute_retry_delay_seconds(attempt_count)
                retry_scheduled_at = datetime.now(timezone.utc) + timedelta(
                    seconds=retry_delay_seconds
                )
                retried_job = await self.operational_store.mark_job_for_retry(
                    job.job_id,
                    worker_id=self.worker_id,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    retry_scheduled_at=retry_scheduled_at,
                    retry_delay_seconds=retry_delay_seconds,
                )
                await self.operational_store.record_audit_event(
                    event_type="automation_job_retry_scheduled",
                    actor_external_id=job.requested_by,
                    actor_role="automation-admin",
                    ticket_id=job.ticket_id,
                    source_channel="automation-worker",
                    status="retry-scheduled",
                    payload_json={
                        "automation_name": job.automation_name,
                        "worker_id": self.worker_id,
                        "job_id": job.job_id,
                        "attempt_count": attempt_count,
                        "max_attempts": max_attempts,
                        "retry_delay_seconds": retry_delay_seconds,
                        "retry_scheduled_at": retry_scheduled_at.isoformat(),
                    },
                )
                return retried_job is not None

            dead_letter_enqueue = await self.job_queue.enqueue_job(job.job_id, dead_letter=True)
            terminal_job = await self.operational_store.mark_job_dead_letter(
                job.job_id,
                worker_id=self.worker_id,
                queue_mode=dead_letter_enqueue.queue_mode,
                queue_key=dead_letter_enqueue.queue_key,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
            await self.operational_store.annotate_job_queue(
                job.job_id,
                queue_mode=dead_letter_enqueue.queue_mode,
                queue_key=dead_letter_enqueue.queue_key,
                notes=dead_letter_enqueue.notes,
                dead_letter=True,
            )
            await self.operational_store.record_audit_event(
                event_type="automation_job_dead_lettered",
                actor_external_id=job.requested_by,
                actor_role="automation-admin",
                ticket_id=job.ticket_id,
                source_channel="automation-worker",
                status="dead-letter",
                payload_json={
                    "automation_name": job.automation_name,
                    "worker_id": self.worker_id,
                    "job_id": job.job_id,
                    "attempt_count": attempt_count,
                    "max_attempts": max_attempts,
                },
            )
            return terminal_job is not None

        completed_job = await self.operational_store.finalize_job_execution(
            job.job_id,
            worker_id=self.worker_id,
            execution_status=result.execution_status,
            result_payload=result.result_payload,
            notes=result.notes,
        )
        await self.operational_store.record_audit_event(
            event_type="automation_job_completed",
            actor_external_id=job.requested_by,
            actor_role="automation-admin",
            ticket_id=job.ticket_id,
            source_channel="automation-worker",
            status=result.execution_status,
            payload_json={
                "automation_name": job.automation_name,
                "worker_id": self.worker_id,
                "job_id": job.job_id,
            },
        )
        return completed_job is not None

    def _compute_retry_delay_seconds(self, attempt_count: int) -> int:
        exponent = max(attempt_count - 1, 0)
        retry_delay_seconds = self.job_queue.settings.automation_retry_base_seconds * (2**exponent)
        return min(retry_delay_seconds, self.job_queue.settings.automation_retry_max_seconds)

    async def _audit_blocked_queue_item(self, job_id: str) -> None:
        job = await self.operational_store.get_job_request(job_id)
        block_reason = "job_not_found"
        actor_external_id: str | None = None
        ticket_id: str | None = None
        payload_json: dict[str, object] = {
            "job_id": job_id,
        }

        if job is not None:
            actor_external_id = job.requested_by
            ticket_id = job.ticket_id
            payload_json["automation_name"] = job.automation_name
            payload_json["approval_status"] = job.approval_status
            payload_json["execution_status"] = job.execution_status
            if job.approval_status != "approved":
                block_reason = "approval_status_not_approved"
            elif job.execution_status != "queued":
                block_reason = "execution_status_not_queued"
            else:
                block_reason = "job_not_acquired"

        payload_json["block_reason"] = block_reason
        await self.operational_store.record_audit_event(
            event_type="automation_job_blocked",
            actor_external_id=actor_external_id,
            actor_role="automation-admin",
            ticket_id=ticket_id,
            source_channel="automation-worker",
            status="blocked",
            payload_json=payload_json,
        )

    async def run_forever(
        self,
        *,
        timeout_seconds: int = 5,
        idle_sleep_seconds: float = 1.0,
    ) -> None:
        while True:
            processed = await self.run_once(timeout_seconds=timeout_seconds)
            if not processed:
                await asyncio.sleep(idle_sleep_seconds)


async def main() -> None:
    settings = get_settings()
    worker = AutomationWorker(
        operational_store=OperationalStateStore(settings),
        job_queue=JobQueueService(settings),
        automation_service=AutomationService(GLPIClient(settings)),
    )
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())