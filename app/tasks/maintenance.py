"""
Maintenance tasks: account deletion.
"""
import logging

from app.celery_config import celery_app
from app.services.deletion_service import delete_user_account

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, name="tasks.delete_user_account")
def delete_user_account_task(self, user_id: str) -> dict:
    """Delete every record and file of a user (idempotent, safe to retry)."""
    try:
        return delete_user_account(user_id)
    except Exception as e:
        logger.error("Account deletion for %s failed: %s", user_id, e, exc_info=True)
        raise self.retry(exc=e, countdown=60)
