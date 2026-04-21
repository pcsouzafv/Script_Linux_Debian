import asyncio

from app.core.config import Settings
from app.services.job_queue import (
    JobQueueService,
    clear_memory_job_queue,
    get_memory_dead_letter_job_queue_items,
    get_memory_job_queue_items,
)


def test_memory_job_queue_enqueues_and_dequeues_job_ids() -> None:
    clear_memory_job_queue()
    queue = JobQueueService(Settings(_env_file=None, redis_url=None))

    enqueue_result = asyncio.run(queue.enqueue_job("job-123"))
    dequeue_result = asyncio.run(queue.dequeue_job(timeout_seconds=0))

    assert enqueue_result.queue_mode == "memory"
    assert dequeue_result is not None
    assert dequeue_result.job_id == "job-123"
    assert dequeue_result.queue_mode == "memory"


def test_memory_job_queue_stores_dead_letter_jobs_separately() -> None:
    clear_memory_job_queue()
    queue = JobQueueService(Settings(_env_file=None, redis_url=None))

    enqueue_result = asyncio.run(queue.enqueue_job("job-dead", dead_letter=True))

    assert enqueue_result.queue_mode == "memory"
    assert enqueue_result.queue_key == queue.dead_letter_queue_key
    assert get_memory_job_queue_items() == []
    assert get_memory_dead_letter_job_queue_items() == ["job-dead"]