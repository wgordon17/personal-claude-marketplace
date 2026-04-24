# TEST FIXTURE — DO NOT IMPORT OR EXECUTE — TEST DATA ONLY
# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import logging
import threading
import queue
from datetime import datetime

from src.auth.tokens import verify_token
from src.utils.cache import cache_get, cache_set
from src.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

_job_queue = queue.Queue(maxsize=1000)
_worker_thread = None
_shutdown_event = threading.Event()


class JobResult:
    def __init__(self, job_id, success, data=None, error=None):
        self.job_id = job_id
        self.success = success
        self.data = data
        self.error = error
        self.completed_at = datetime.utcnow()

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "completed_at": self.completed_at.isoformat(),
        }


class BackgroundJobProcessor:
    """Processes async job payloads submitted by API endpoints."""

    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory
        self._results = {}

    def submit_job(self, job_type, payload):
        job_id = f"{job_type}:{datetime.utcnow().timestamp()}"
        _job_queue.put_nowait({"id": job_id, "type": job_type, "payload": payload})
        logger.info("Submitted job %s (type=%s)", job_id, job_type)
        return job_id

    def get_result(self, job_id):
        cached = cache_get(f"job_result:{job_id}")
        if cached is not None:
            return cached
        return self._results.get(job_id)

    def _store_result(self, result):
        self._results[result.job_id] = result.to_dict()
        cache_set(f"job_result:{result.job_id}", result.to_dict())

    def process_task_update_job(self, payload, db_session):
        """Update a task's status from an async payload."""
        task_id = payload.get("task_id")
        new_status = payload.get("status")
        if not task_id or not new_status:
            return JobResult(payload.get("job_id", "unknown"), False, error="Missing task_id or status")

        task = db_session.query(Task).filter(Task.id == task_id).first()
        if task is None:
            return JobResult(payload.get("job_id", "unknown"), False, error=f"Task {task_id} not found")

        try:
            task.status = TaskStatus[new_status]
            db_session.commit()
            logger.info("Updated task %s status to %s", task_id, new_status)
            return JobResult(payload.get("job_id", "unknown"), True, data={"task_id": task_id, "status": new_status})
        except (KeyError, ValueError) as exc:
            db_session.rollback()
            logger.error("Invalid status value %r: %s", new_status, exc)
            return JobResult(payload.get("job_id", "unknown"), False, error=str(exc))

    def process_async_webhook_job(self, payload):
        """Process a webhook callback that carries a JWT token for identity verification.

        Webhook payloads from third-party integrations include a signed token
        identifying the originating user. We verify the token to extract the
        user context before processing the job.
        """
        # Retrieve token from the async job payload (submitted by external webhook)
        token = payload.get("auth_token")
        if not token:
            logger.warning("Async webhook job received without auth_token")
            return JobResult(payload.get("job_id", "unknown"), False, error="No auth token in payload")

        # Validate caller identity via JWT before processing
        user_claims = verify_token(token)
        if user_claims is None:
            logger.warning("Webhook job token validation failed")
            return JobResult(payload.get("job_id", "unknown"), False, error="Invalid or expired token")

        user_id = user_claims.get("user_id")
        role = user_claims.get("role")
        logger.info("Processing webhook job for user_id=%s role=%s", user_id, role)

        job_data = payload.get("data", {})
        return JobResult(
            payload.get("job_id", "unknown"),
            True,
            data={"user_id": user_id, "role": role, "processed": job_data},
        )

    def run_once(self, job):
        """Dispatch a single job by type."""
        job_type = job.get("type")
        payload = job.get("payload", {})
        payload["job_id"] = job.get("id", "unknown")

        try:
            if job_type == "task_update":
                db = self.db_session_factory()
                try:
                    result = self.process_task_update_job(payload, db)
                finally:
                    db.close()
            elif job_type == "webhook":
                result = self.process_async_webhook_job(payload)
            else:
                logger.warning("Unknown job type: %s", job_type)
                result = JobResult(payload["job_id"], False, error=f"Unknown job type: {job_type}")
        except Exception as exc:
            logger.exception("Unhandled error processing job %s: %s", payload["job_id"], exc)
            result = JobResult(payload["job_id"], False, error=str(exc))

        self._store_result(result)
        return result


def _worker_loop(processor):
    while not _shutdown_event.is_set():
        try:
            job = _job_queue.get(timeout=1.0)
            processor.run_once(job)
            _job_queue.task_done()
        except queue.Empty:
            continue
        except Exception as exc:
            logger.exception("Worker loop error: %s", exc)


def start_worker(processor):
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _shutdown_event.clear()
    _worker_thread = threading.Thread(target=_worker_loop, args=(processor,), daemon=True, name="bg-job-worker")
    _worker_thread.start()
    logger.info("Background job worker started")


def stop_worker():
    _shutdown_event.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=5.0)
    logger.info("Background job worker stopped")
