"""
Celery configuration for async task processing
"""
from celery import Celery
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
)
