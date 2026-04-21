from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings

try:
    from redis.asyncio import from_url as redis_from_url
except ImportError:  # pragma: no cover - fallback remains available without the driver
    redis_from_url = None


DEFAULT_AUTOMATION_QUEUE_KEY = "helpdesk:automation:jobs"
DEFAULT_AUTOMATION_DEAD_LETTER_QUEUE_KEY = "helpdesk:automation:jobs:dead-letter"
_MEMORY_JOB_QUEUE: list[str] = []
_MEMORY_DEAD_LETTER_JOB_QUEUE: list[str] = []


def clear_memory_job_queue() -> None:
    _MEMORY_JOB_QUEUE.clear()
    _MEMORY_DEAD_LETTER_JOB_QUEUE.clear()


def get_memory_job_queue_items() -> list[str]:
    return list(_MEMORY_JOB_QUEUE)


def get_memory_dead_letter_job_queue_items() -> list[str]:
    return list(_MEMORY_DEAD_LETTER_JOB_QUEUE)


@dataclass(slots=True)
class JobQueueEnqueueResult:
    queue_mode: str
    queue_key: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobQueueDequeueResult:
    job_id: str
    queue_mode: str
    queue_key: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobQueueRemoveResult:
    queue_mode: str
    queue_key: str
    removed_count: int
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobQueueSnapshot:
    queue_mode: str
    queue_key: str
    dead_letter_queue_key: str
    queue_depth: int
    dead_letter_queue_depth: int
    notes: list[str] = field(default_factory=list)


class JobQueueService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.queue_key = DEFAULT_AUTOMATION_QUEUE_KEY
        self.dead_letter_queue_key = DEFAULT_AUTOMATION_DEAD_LETTER_QUEUE_KEY

    async def enqueue_job(
        self,
        job_id: str,
        *,
        dead_letter: bool = False,
    ) -> JobQueueEnqueueResult:
        normalized_job_id = str(job_id).strip()
        if not normalized_job_id:
            raise ValueError("job_id deve ser informado antes de entrar na fila.")

        queue_key = self.dead_letter_queue_key if dead_letter else self.queue_key

        client = self._open_client()
        if client is not None:
            try:
                await client.rpush(queue_key, normalized_job_id)
                return JobQueueEnqueueResult(queue_mode="redis", queue_key=queue_key)
            except Exception:
                notes = [
                    (
                        "Redis indisponivel; job enviado para a fila de dead-letter em memoria do processo atual."
                        if dead_letter
                        else "Redis indisponivel; job enfileirado no fallback em memoria do processo atual."
                    )
                ]
            finally:
                await client.aclose()
        else:
            notes = self._memory_fallback_notes()

        if dead_letter:
            _MEMORY_DEAD_LETTER_JOB_QUEUE.append(normalized_job_id)
        else:
            _MEMORY_JOB_QUEUE.append(normalized_job_id)
        return JobQueueEnqueueResult(
            queue_mode="memory",
            queue_key=queue_key,
            notes=notes,
        )

    async def dequeue_job(
        self,
        *,
        timeout_seconds: int = 5,
    ) -> JobQueueDequeueResult | None:
        client = self._open_client()
        if client is not None:
            try:
                if timeout_seconds <= 0:
                    job_id = await client.lpop(self.queue_key)
                    if job_id is None:
                        return None
                    return JobQueueDequeueResult(
                        job_id=str(job_id),
                        queue_mode="redis",
                        queue_key=self.queue_key,
                    )

                result = await client.blpop(self.queue_key, timeout=timeout_seconds)
                if result is None:
                    return None
                _, job_id = result
                return JobQueueDequeueResult(
                    job_id=str(job_id),
                    queue_mode="redis",
                    queue_key=self.queue_key,
                )
            except Exception:
                notes = [
                    "Redis indisponivel; worker consumindo a fila de fallback em memoria do processo atual."
                ]
            finally:
                await client.aclose()
        else:
            notes = self._memory_fallback_notes()

        if not _MEMORY_JOB_QUEUE:
            return None

        return JobQueueDequeueResult(
            job_id=_MEMORY_JOB_QUEUE.pop(0),
            queue_mode="memory",
            queue_key=self.queue_key,
            notes=notes,
        )

    async def get_queue_snapshot(self) -> JobQueueSnapshot:
        client = self._open_client()
        if client is not None:
            try:
                queue_depth = int(await client.llen(self.queue_key))
                dead_letter_queue_depth = int(await client.llen(self.dead_letter_queue_key))
                return JobQueueSnapshot(
                    queue_mode="redis",
                    queue_key=self.queue_key,
                    dead_letter_queue_key=self.dead_letter_queue_key,
                    queue_depth=queue_depth,
                    dead_letter_queue_depth=dead_letter_queue_depth,
                )
            except Exception:
                notes = [
                    "Redis indisponivel; resumo da fila retornado a partir do fallback em memoria do processo atual."
                ]
            finally:
                await client.aclose()
        else:
            notes = self._memory_fallback_notes()

        return JobQueueSnapshot(
            queue_mode="memory",
            queue_key=self.queue_key,
            dead_letter_queue_key=self.dead_letter_queue_key,
            queue_depth=len(_MEMORY_JOB_QUEUE),
            dead_letter_queue_depth=len(_MEMORY_DEAD_LETTER_JOB_QUEUE),
            notes=notes,
        )

    async def remove_job(self, job_id: str) -> JobQueueRemoveResult:
        normalized_job_id = str(job_id).strip()
        if not normalized_job_id:
            raise ValueError("job_id deve ser informado antes de remover da fila.")

        client = self._open_client()
        if client is not None:
            try:
                removed_count = int(await client.lrem(self.queue_key, 0, normalized_job_id))
                notes: list[str] = []
                if removed_count == 0:
                    notes.append(
                        "job_id nao estava mais presente na fila principal no momento do cancelamento."
                    )
                return JobQueueRemoveResult(
                    queue_mode="redis",
                    queue_key=self.queue_key,
                    removed_count=removed_count,
                    notes=notes,
                )
            except Exception:
                notes = [
                    "Redis indisponivel; remocao da fila principal executada apenas no fallback em memoria do processo atual."
                ]
            finally:
                await client.aclose()
        else:
            notes = self._memory_fallback_notes()

        removed_count = 0
        while normalized_job_id in _MEMORY_JOB_QUEUE:
            _MEMORY_JOB_QUEUE.remove(normalized_job_id)
            removed_count += 1

        if removed_count == 0:
            notes = [
                *notes,
                "job_id nao estava mais presente na fila principal no momento do cancelamento.",
            ]

        return JobQueueRemoveResult(
            queue_mode="memory",
            queue_key=self.queue_key,
            removed_count=removed_count,
            notes=notes,
        )

    def _open_client(self):
        if not self.settings.redis_url or redis_from_url is None:
            return None
        return redis_from_url(
            self.settings.redis_url,
            decode_responses=True,
            encoding="utf-8",
        )

    def _memory_fallback_notes(self) -> list[str]:
        if self.settings.redis_url:
            return [
                "Redis nao respondeu; o fallback em memoria cobre apenas o processo atual."
            ]
        return [
            "Redis nao configurado; usando fila em memoria apenas para desenvolvimento e testes."
        ]