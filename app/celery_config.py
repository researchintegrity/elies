"""
Celery configuration for async task processing
"""
from celery import Celery
import os
from app.config.settings import (
    CELERY_TASK_TIME_LIMIT,
    CELERY_TASK_SOFT_TIME_LIMIT,
    CELERY_MAX_RETRIES,
    CELERY_TASK_DEFAULT_RETRY_DELAY,
    CELERY_RESULT_EXPIRES,
    CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
    CELERY_REDIS_SOCKET_TIMEOUT,
)

# Redis connection settings
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB + 1}"

# Create Celery app
celery_app = Celery(
    "elies_tasks",
    broker=broker_url,
    backend=result_backend,
    include=[
        "app.tasks.copy_move_detection",
        "app.tasks.trufor",
        "app.tasks.image_extraction",
        "app.tasks.panel_extraction",
        "app.tasks.watermark_removal"
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
    
    # Retry settings
    task_max_retries=CELERY_MAX_RETRIES,
    task_default_retry_delay=CELERY_TASK_DEFAULT_RETRY_DELAY,
    
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
