"""Celery tasks for restaurant qualification (prospect-promotion path).

Wraps the existing `qualify_restaurant` service (src/services/qualification.py)
for async invocation from the prospect-to-outreach promotion orchestrator
(NIF-284). When the associated Lead is provided and qualification status is
"qualified", the Lead's lifecycle_stage and status are updated to "qualified".
"""

from src.db.session import async_session
from src.tasks.celery_app import celery_app
from src.tasks.crawl_tasks import run_async
from src.utils.logging import get_logger

logger = get_logger("tasks.qualification")


@celery_app.task(
    name="src.tasks.qualification_tasks.qualify_restaurant_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def qualify_restaurant_task(
    self, restaurant_id: str, lead_id: str | None = None
) -> dict:
    """Run qualification for a single restaurant and link result to a Lead if provided.

    Args:
        restaurant_id: UUID of the Restaurant to qualify (as string).
        lead_id: Optional UUID of an associated Lead (as string). When provided
            and qualification returns "qualified", the Lead's lifecycle_stage
            and status are updated to "qualified".

    Returns:
        A dict with keys: status, confidence, qualification_result_id.
    """
    logger.info("qualify_task_started", restaurant_id=restaurant_id, lead_id=lead_id)

    async def _run():
        from uuid import UUID

        from src.db.models import Lead
        from src.services.qualification import qualify_restaurant

        async with async_session() as session:
            qual = await qualify_restaurant(session, UUID(restaurant_id))

            # Promote Lead lifecycle stage if qualification passed
            if lead_id and qual.qualification_status == "qualified":
                lead = await session.get(Lead, UUID(lead_id))
                if lead is not None:
                    lead.lifecycle_stage = "qualified"
                    lead.status = "qualified"
                    # NOTE: a fuller implementation would also write a
                    # LeadStageHistory row; the existing lead service handles
                    # that — call it if/when integrated with that pipeline.

            await session.commit()
            return {
                "status": qual.qualification_status,
                "confidence": qual.confidence_score,
                "qualification_result_id": str(qual.id),
            }

    try:
        return run_async(_run())
    except Exception as exc:
        logger.error(
            "qualify_task_failed", restaurant_id=restaurant_id, error=str(exc)
        )
        raise self.retry(exc=exc)
