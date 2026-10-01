"""
Celery configuration for async task processing
"""
from celery import Celery
from celery.signals import (
    before_task_publish,
    setup_logging,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
)
from app.config.settings import (
    CELERY_BROKER_URL,
    CELERY_RESULT_BACKEND,
    CELERY_TASK_TIME_LIMIT,
    CELERY_TASK_SOFT_TIME_LIMIT,
    CELERY_RESULT_EXPIRES,
    CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
    CELERY_REDIS_SOCKET_TIMEOUT,
)

broker_url = CELERY_BROKER_URL
result_backend = CELERY_RESULT_BACKEND

# Create Celery app
celery_app = Celery(
    "elies_tasks",
    broker=broker_url,
    backend=result_backend,
    # Every module that defines tasks must be listed: workers only import these
    include=[
        "app.tasks.cbir",
        "app.tasks.copy_move_detection",
        "app.tasks.image_extraction",
        "app.tasks.maintenance",
        "app.tasks.panel_extraction",
        "app.tasks.provenance",
        "app.tasks.trufor",
        "app.tasks.watermark_removal",
    ]
)

# Configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution settings
    task_track_started=True,
    task_time_limit=CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=CELERY_TASK_SOFT_TIME_LIMIT,
    task_acks_late=True,  # Acknowledge after task completes
    
    # Retry limits are set per task (max_retries=CELERY_MAX_RETRIES)

    # Result backend settings
    result_expires=CELERY_RESULT_EXPIRES,
    result_backend_transport_options={
        "socket_connect_timeout": CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
        "socket_timeout": CELERY_REDIS_SOCKET_TIMEOUT,
        "retry_on_timeout": True,
    },
    
    # Worker settings
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,

    # Periodic maintenance, run by the 'beat' service in docker compose
    beat_schedule={
        "reap-stale-jobs": {"task": "tasks.reap_stale_jobs", "schedule": 600.0},
        "reconcile-storage": {"task": "tasks.reconcile_storage", "schedule": 86400.0},
    },
)


@worker_ready.connect
def kill_orphaned_tool_containers(**kwargs):
    """Kill tool containers this worker left running before a crash or restart."""
    from app.utils.docker_runner import kill_orphaned_containers

    kill_orphaned_containers()


@worker_process_init.connect
def reset_database_connection(**kwargs):
    """
    Each forked worker process opens its own MongoDB connection (creating
    indexes on first use): a MongoClient must not be shared across fork.
    """
    from app.db.mongodb import db_connection

    db_connection.reset()


@setup_logging.connect
def configure_worker_logging(**kwargs):
    """Workers log like the API (LOG_LEVEL, LOG_FORMAT, request IDs) instead of Celery's default setup."""
    from app.config.settings import LOG_FORMAT, LOG_LEVEL
    from app.logging_config import configure_logging

    configure_logging(LOG_LEVEL, LOG_FORMAT)


@before_task_publish.connect
def propagate_request_id(headers=None, **kwargs):
    """Tasks queued while handling an API request carry its request ID."""
    from app.request_context import current_request_id

    request_id = current_request_id()
    if request_id and headers is not None:
        headers.setdefault("request_id", request_id)


@task_prerun.connect
def bind_request_id(task=None, **kwargs):
    """Log records of a task carry the request ID of the API request that queued it."""
    from app.request_context import request_id_var

    request = getattr(task, "request", None)
    request_id = getattr(request, "request_id", None) or (getattr(request, "headers", None) or {}).get("request_id")
    request_id_var.set(request_id)


@task_postrun.connect
def unbind_request_id(**kwargs):
    from app.request_context import request_id_var

    request_id_var.set(None)
