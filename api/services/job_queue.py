"""
Async Job Queue for Machine Operations

Provides a scalable, resilient job queue for handling:
- Machine start/stop/reset operations
- VPN configuration generation
- Background cleanup tasks

Features:
- Priority queue with different job types
- Concurrent execution with configurable workers
- Job deduplication
- Status tracking and callbacks
- Graceful shutdown
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, Callable, Awaitable, List
from collections import defaultdict
import heapq

logger = logging.getLogger("vulnlab.job_queue")


class JobStatus(str, Enum):
    """Job status states."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class JobPriority(int, Enum):
    """Job priority levels (lower = higher priority)."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


class JobType(str, Enum):
    """Types of jobs."""
    MACHINE_START = "machine_start"
    MACHINE_STOP = "machine_stop"
    MACHINE_RESET = "machine_reset"
    MACHINE_EXTEND = "machine_extend"
    CHAIN_START = "chain_start"
    CHAIN_STOP = "chain_stop"
    VPN_GENERATE = "vpn_generate"
    VPN_REVOKE = "vpn_revoke"
    CLEANUP = "cleanup"
    HEALTH_CHECK = "health_check"


@dataclass(order=True)
class Job:
    """Represents a job in the queue."""
    priority: int
    created_at: datetime = field(compare=False)
    job_id: str = field(compare=False, default_factory=lambda: str(uuid.uuid4()))
    job_type: JobType = field(compare=False, default=JobType.MACHINE_START)
    user_id: Optional[int] = field(compare=False, default=None)
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)
    status: JobStatus = field(compare=False, default=JobStatus.PENDING)
    result: Optional[Any] = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    attempts: int = field(compare=False, default=0)
    max_attempts: int = field(compare=False, default=3)
    started_at: Optional[datetime] = field(compare=False, default=None)
    completed_at: Optional[datetime] = field(compare=False, default=None)
    callback: Optional[Callable] = field(compare=False, default=None)
    dedup_key: Optional[str] = field(compare=False, default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "user_id": self.user_id,
            "status": self.status.value,
            "priority": self.priority,
            "attempts": self.attempts,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


class JobQueue:
    """
    Async job queue with priority support and concurrent execution.

    Usage:
        queue = JobQueue(max_workers=5)
        await queue.start()

        job_id = await queue.enqueue(
            job_type=JobType.MACHINE_START,
            user_id=123,
            payload={"template_id": "100", "instance_name": "test"},
            priority=JobPriority.NORMAL,
        )

        status = queue.get_job_status(job_id)
        await queue.stop()
    """

    def __init__(
        self,
        max_workers: int = 5,
        max_queue_size: int = 1000,
        job_timeout: int = 600,
    ):
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.job_timeout = job_timeout

        self._queue: List[Job] = []
        self._jobs: Dict[str, Job] = {}
        self._running_jobs: Dict[str, Job] = {}
        self._dedup_keys: Dict[str, str] = {}  # dedup_key -> job_id
        self._handlers: Dict[JobType, Callable[[Job], Awaitable[Any]]] = {}

        self._lock = asyncio.Lock()
        self._queue_event = asyncio.Event()
        self._workers: List[asyncio.Task] = []
        self._running = False

        # Metrics
        self._metrics = {
            "total_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "retried_jobs": 0,
        }

    def register_handler(self, job_type: JobType, handler: Callable[[Job], Awaitable[Any]]):
        """Register a handler for a job type."""
        self._handlers[job_type] = handler
        logger.info(f"Registered handler for {job_type.value}")

    async def start(self):
        """Start the job queue workers."""
        if self._running:
            return

        self._running = True

        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)

        logger.info(f"Job queue started with {self.max_workers} workers")

    async def stop(self, timeout: int = 30):
        """Stop the job queue gracefully."""
        self._running = False
        self._queue_event.set()

        # Wait for workers to finish
        if self._workers:
            done, pending = await asyncio.wait(
                self._workers,
                timeout=timeout,
            )

            for task in pending:
                task.cancel()

        self._workers.clear()
        logger.info("Job queue stopped")

    async def enqueue(
        self,
        job_type: JobType,
        user_id: Optional[int] = None,
        payload: Dict[str, Any] = None,
        priority: JobPriority = JobPriority.NORMAL,
        callback: Callable = None,
        dedup_key: str = None,
    ) -> str:
        """
        Add a job to the queue.

        Args:
            job_type: Type of job to execute
            user_id: User who initiated the job
            payload: Job-specific data
            priority: Job priority (lower = higher priority)
            callback: Async function to call when job completes
            dedup_key: Optional key for deduplication

        Returns:
            Job ID string
        """
        async with self._lock:
            # Check queue size
            if len(self._queue) >= self.max_queue_size:
                raise RuntimeError("Job queue is full")

            # Check deduplication
            if dedup_key and dedup_key in self._dedup_keys:
                existing_job_id = self._dedup_keys[dedup_key]
                existing_job = self._jobs.get(existing_job_id)
                if existing_job and existing_job.status in [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]:
                    logger.info(f"Deduped job with key {dedup_key}, returning existing job {existing_job_id}")
                    return existing_job_id

            # Create job
            job = Job(
                priority=priority.value,
                created_at=datetime.utcnow(),
                job_type=job_type,
                user_id=user_id,
                payload=payload or {},
                status=JobStatus.QUEUED,
                callback=callback,
                dedup_key=dedup_key,
            )

            # Add to queue and tracking
            heapq.heappush(self._queue, job)
            self._jobs[job.job_id] = job

            if dedup_key:
                self._dedup_keys[dedup_key] = job.job_id

            self._metrics["total_jobs"] += 1
            self._queue_event.set()

            logger.info(f"Enqueued job {job.job_id} ({job_type.value}) for user {user_id}")
            return job.job_id

    async def cancel(self, job_id: str) -> bool:
        """Cancel a pending or queued job."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status in [JobStatus.PENDING, JobStatus.QUEUED]:
                job.status = JobStatus.CANCELLED
                job.completed_at = datetime.utcnow()

                # Remove from dedup
                if job.dedup_key:
                    self._dedup_keys.pop(job.dedup_key, None)

                logger.info(f"Cancelled job {job_id}")
                return True

            return False

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        return self._jobs.get(job_id)

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status as dict."""
        job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def get_user_jobs(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent jobs for a user."""
        user_jobs = [
            job for job in self._jobs.values()
            if job.user_id == user_id
        ]
        user_jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [job.to_dict() for job in user_jobs[:limit]]

    def get_queue_status(self) -> Dict[str, Any]:
        """Get queue status and metrics."""
        status_counts = defaultdict(int)
        for job in self._jobs.values():
            status_counts[job.status.value] += 1

        return {
            "queue_length": len(self._queue),
            "running_jobs": len(self._running_jobs),
            "total_jobs": self._metrics["total_jobs"],
            "completed_jobs": self._metrics["completed_jobs"],
            "failed_jobs": self._metrics["failed_jobs"],
            "retried_jobs": self._metrics["retried_jobs"],
            "by_status": dict(status_counts),
            "workers": self.max_workers,
            "is_running": self._running,
        }

    async def _worker(self, worker_id: int):
        """Worker coroutine that processes jobs."""
        logger.debug(f"Worker {worker_id} started")

        while self._running:
            try:
                # Wait for jobs
                await self._queue_event.wait()

                if not self._running:
                    break

                # Get next job
                job = await self._get_next_job()
                if not job:
                    self._queue_event.clear()
                    continue

                # Process job
                await self._process_job(job, worker_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")

        logger.debug(f"Worker {worker_id} stopped")

    async def _get_next_job(self) -> Optional[Job]:
        """Get the next job from the queue."""
        async with self._lock:
            while self._queue:
                job = heapq.heappop(self._queue)

                # Skip cancelled jobs
                if job.status == JobStatus.CANCELLED:
                    continue

                # Check if job is still valid
                if job.job_id in self._jobs:
                    return job

            return None

    async def _process_job(self, job: Job, worker_id: int):
        """Process a single job."""
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.attempts += 1
        self._running_jobs[job.job_id] = job

        logger.info(f"Worker {worker_id} processing job {job.job_id} ({job.job_type.value}), attempt {job.attempts}")

        try:
            # Get handler
            handler = self._handlers.get(job.job_type)
            if not handler:
                raise RuntimeError(f"No handler for job type {job.job_type}")

            # Execute with timeout
            result = await asyncio.wait_for(
                handler(job),
                timeout=self.job_timeout,
            )

            # Success
            job.status = JobStatus.COMPLETED
            job.result = result
            job.completed_at = datetime.utcnow()
            self._metrics["completed_jobs"] += 1

            logger.info(f"Job {job.job_id} completed successfully")

            # Call callback if provided
            if job.callback:
                try:
                    await job.callback(job)
                except Exception as e:
                    logger.error(f"Job callback error: {e}")

        except asyncio.TimeoutError:
            job.error = "Job timed out"
            await self._handle_job_failure(job)

        except Exception as e:
            job.error = str(e)
            logger.error(f"Job {job.job_id} failed: {e}")
            await self._handle_job_failure(job)

        finally:
            self._running_jobs.pop(job.job_id, None)

            # Remove dedup key on completion
            if job.dedup_key and job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
                async with self._lock:
                    self._dedup_keys.pop(job.dedup_key, None)

    async def _handle_job_failure(self, job: Job):
        """Handle job failure with retry logic."""
        if job.attempts < job.max_attempts:
            # Retry
            job.status = JobStatus.RETRYING
            self._metrics["retried_jobs"] += 1

            # Re-queue with backoff
            delay = min(30, 2 ** job.attempts)
            await asyncio.sleep(delay)

            async with self._lock:
                job.status = JobStatus.QUEUED
                heapq.heappush(self._queue, job)
                self._queue_event.set()

            logger.info(f"Job {job.job_id} queued for retry (attempt {job.attempts + 1})")

        else:
            # Final failure
            job.status = JobStatus.FAILED
            job.completed_at = datetime.utcnow()
            self._metrics["failed_jobs"] += 1

            logger.error(f"Job {job.job_id} failed permanently after {job.attempts} attempts")

    async def cleanup_old_jobs(self, max_age_hours: int = 24):
        """Remove old completed/failed jobs from memory."""
        async with self._lock:
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
            to_remove = []

            for job_id, job in self._jobs.items():
                if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
                    if job.completed_at and job.completed_at < cutoff:
                        to_remove.append(job_id)

            for job_id in to_remove:
                del self._jobs[job_id]

            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old jobs")


# Global job queue instance
_job_queue: Optional[JobQueue] = None


async def get_job_queue() -> JobQueue:
    """Get or create the global job queue."""
    global _job_queue

    if _job_queue is None:
        from config import settings

        max_workers = getattr(settings, 'job_queue_workers', 5)
        _job_queue = JobQueue(max_workers=max_workers)

        # Register handlers
        from .job_handlers import register_all_handlers
        register_all_handlers(_job_queue)

        await _job_queue.start()

    return _job_queue
