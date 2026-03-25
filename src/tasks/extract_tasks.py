"""Celery tasks for LLM-based data extraction."""

from src.tasks.celery_app import celery_app
from src.tasks.crawl_tasks import run_async
from src.utils.logging import get_logger

logger = get_logger("tasks.extract")


@celery_app.task(bind=True, name="src.tasks.extract_tasks.extract_records")
def extract_records(self, restaurant_ids: list[str] | None = None):
    """Run LLM extraction on unprocessed source records."""

    async def _extract():
        from src.db.session import async_session
        from src.db.models import SourceRecord
        from src.extraction.extractor import extractor
        from sqlalchemy import select, update

        async with async_session() as session:
            query = select(SourceRecord).where(SourceRecord.extracted_data.is_(None))
            if restaurant_ids:
                query = query.where(SourceRecord.restaurant_id.in_(restaurant_ids))
            query = query.limit(100)

            result = await session.execute(query)
            records = result.scalars().all()

            logger.info("extracting_records", count=len(records))
            processed = 0

            for record in records:
                try:
                    raw_data = record.raw_data or {}
                    extracted = await extractor.extract_and_enrich(raw_data)

                    await session.execute(
                        update(SourceRecord)
                        .where(SourceRecord.id == record.id)
                        .values(extracted_data=extracted)
                    )
                    processed += 1

                except Exception as e:
                    logger.error("extraction_error", record_id=str(record.id), error=str(e))
                    continue

            await session.commit()
            logger.info("extraction_complete", processed=processed)
            return {"processed": processed}

    return run_async(_extract())
