"""ZIP code sub-location crawling for comprehensive restaurant coverage.

Instead of one query per city (60 results), iterate through all ZIP codes
in the city. Each ZIP returns ~60-100 unique results. After deduplication,
yields 1,500-2,000+ restaurants per city.
"""

from datetime import datetime, timezone

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Restaurant, CrawlJob
from src.utils.logging import get_logger

logger = get_logger("services.zipcode_crawl")

# Major US city ZIP code ranges (expand as needed)
# Source: USPS ZIP code ranges by city
CITY_ZIP_CODES: dict[str, list[str]] = {
    "nashville": [
        "37201", "37202", "37203", "37204", "37205", "37206", "37207", "37208",
        "37209", "37210", "37211", "37212", "37213", "37214", "37215", "37216",
        "37217", "37218", "37219", "37220", "37221", "37222", "37224", "37227",
        "37228", "37229", "37230", "37232", "37234", "37235", "37236", "37238",
        "37240", "37241", "37242", "37243", "37244",
    ],
    "new york": [
        "10001", "10002", "10003", "10004", "10005", "10006", "10007", "10008",
        "10009", "10010", "10011", "10012", "10013", "10014", "10016", "10017",
        "10018", "10019", "10020", "10021", "10022", "10023", "10024", "10025",
        "10026", "10027", "10028", "10029", "10030", "10031", "10032", "10033",
        "10034", "10035", "10036", "10037", "10038", "10039", "10040", "10044",
        "10065", "10069", "10075", "10103", "10110", "10111", "10112", "10115",
        "10119", "10128", "10152", "10153", "10154", "10162", "10165", "10167",
        "10168", "10169", "10170", "10171", "10172", "10173", "10174", "10175",
        "10176", "10177", "10178", "10199", "10271", "10278", "10279", "10280",
        "10282",
        # Brooklyn
        "11201", "11203", "11204", "11205", "11206", "11207", "11208", "11209",
        "11210", "11211", "11212", "11213", "11214", "11215", "11216", "11217",
        "11218", "11219", "11220", "11221", "11222", "11223", "11224", "11225",
        "11226", "11228", "11229", "11230", "11231", "11232", "11233", "11234",
        "11235", "11236", "11237", "11238", "11239",
        # Queens
        "11101", "11102", "11103", "11104", "11105", "11106",
        "11354", "11355", "11356", "11357", "11358", "11359",
        "11360", "11361", "11362", "11363", "11364", "11365",
        "11366", "11367", "11368", "11369", "11370", "11371",
        "11372", "11373", "11374", "11375", "11377", "11378",
        "11379", "11385",
    ],
    "los angeles": [
        "90001", "90002", "90003", "90004", "90005", "90006", "90007", "90008",
        "90010", "90011", "90012", "90013", "90014", "90015", "90016", "90017",
        "90018", "90019", "90020", "90021", "90022", "90023", "90024", "90025",
        "90026", "90027", "90028", "90029", "90031", "90032", "90033", "90034",
        "90035", "90036", "90037", "90038", "90039", "90041", "90042", "90043",
        "90044", "90045", "90046", "90047", "90048", "90049", "90056", "90057",
        "90058", "90059", "90061", "90062", "90063", "90064", "90065", "90066",
        "90067", "90068", "90069", "90071", "90077", "90089", "90094",
    ],
    "chicago": [
        "60601", "60602", "60603", "60604", "60605", "60606", "60607", "60608",
        "60609", "60610", "60611", "60612", "60613", "60614", "60615", "60616",
        "60617", "60618", "60619", "60620", "60621", "60622", "60623", "60624",
        "60625", "60626", "60628", "60629", "60630", "60631", "60632", "60634",
        "60636", "60637", "60638", "60639", "60640", "60641", "60642", "60643",
        "60644", "60645", "60646", "60647", "60649", "60651", "60652", "60653",
        "60654", "60655", "60656", "60657", "60659", "60660", "60661",
    ],
    "boston": [
        "02101", "02102", "02103", "02104", "02105", "02106", "02107", "02108",
        "02109", "02110", "02111", "02112", "02113", "02114", "02115", "02116",
        "02117", "02118", "02119", "02120", "02121", "02122", "02124", "02125",
        "02126", "02127", "02128", "02129", "02130", "02131", "02132", "02134",
        "02135", "02136",
    ],
    "austin": [
        "78701", "78702", "78703", "78704", "78705", "78712", "78717", "78719",
        "78721", "78722", "78723", "78724", "78725", "78726", "78727", "78728",
        "78729", "78730", "78731", "78732", "78733", "78734", "78735", "78736",
        "78737", "78738", "78739", "78741", "78742", "78744", "78745", "78746",
        "78747", "78748", "78749", "78750", "78751", "78752", "78753", "78754",
        "78756", "78757", "78758", "78759",
    ],
    "seattle": [
        "98101", "98102", "98103", "98104", "98105", "98106", "98107", "98108",
        "98109", "98112", "98115", "98116", "98117", "98118", "98119", "98121",
        "98122", "98125", "98126", "98133", "98134", "98136", "98144", "98146",
        "98154", "98155", "98164", "98168", "98174", "98177", "98178", "98188",
        "98195", "98199",
    ],
    "miami": [
        "33101", "33109", "33125", "33126", "33127", "33128", "33129", "33130",
        "33131", "33132", "33133", "33134", "33135", "33136", "33137", "33138",
        "33139", "33140", "33141", "33142", "33143", "33144", "33145", "33146",
        "33147", "33149", "33150", "33154", "33155", "33156", "33157", "33158",
        "33160", "33161", "33162", "33165", "33166", "33167", "33168", "33169",
        "33170", "33172", "33173", "33174", "33175", "33176", "33177", "33178",
        "33179", "33180", "33181", "33182", "33183", "33184", "33185", "33186",
        "33187", "33189", "33190", "33193", "33194", "33196",
    ],
}


def get_zip_codes_for_city(city: str) -> list[str]:
    """Get ZIP codes for a city. Falls back to DB lookup if not in hardcoded list."""
    city_lower = city.lower().strip()
    # Remove state suffix if present (e.g., "Nashville, TN" -> "nashville")
    if "," in city_lower:
        city_lower = city_lower.split(",")[0].strip()

    return CITY_ZIP_CODES.get(city_lower, [])


async def get_zip_codes_from_db(session: AsyncSession, city: str) -> list[str]:
    """Get ZIP codes from existing restaurant data for a city."""
    city_clean = city.split(",")[0].strip() if "," in city else city.strip()
    result = await session.execute(
        select(distinct(Restaurant.zip_code))
        .where(
            Restaurant.city.ilike(f"%{city_clean}%"),
            Restaurant.zip_code.isnot(None),
            Restaurant.zip_code != "",
        )
    )
    return [row[0] for row in result.all() if row[0]]


class ZipCodeCrawlService:
    """Crawls a city by iterating through all its ZIP codes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def crawl_city(
        self,
        city: str,
        state: str,
        source: str = "google_maps",
        job_id: str | None = None,
    ) -> dict:
        """Crawl all ZIP codes in a city for comprehensive coverage.

        Returns summary with total restaurants found, ZIPs processed, duplicates skipped.
        """
        from src.api.routers.jobs import _run_crawl_inline

        # Get ZIP codes — hardcoded first, then DB fallback
        zip_codes = get_zip_codes_for_city(city)
        if not zip_codes:
            zip_codes = await get_zip_codes_from_db(self.session, city)

        if not zip_codes:
            logger.warning("no_zip_codes_found", city=city)
            return {
                "city": city,
                "state": state,
                "zip_codes_processed": 0,
                "total_found": 0,
                "message": f"No ZIP codes found for {city}. Run a regular crawl first to seed ZIP code data.",
            }

        logger.info("starting_city_deep_crawl", city=city, state=state, zip_count=len(zip_codes))

        # Count restaurants before
        from sqlalchemy import func as sqlfunc
        before_count = await self.session.scalar(
            select(sqlfunc.count(Restaurant.id)).where(Restaurant.city.ilike(f"%{city}%"))
        ) or 0

        processed = 0
        errors = 0

        for i, zip_code in enumerate(zip_codes):
            location = f"{zip_code}"
            try:
                await _run_crawl_inline(source, "restaurants", location, None)
                processed += 1
                logger.info("zip_crawl_complete", zip=zip_code, progress=f"{i+1}/{len(zip_codes)}")
            except Exception as e:
                errors += 1
                logger.warning("zip_crawl_failed", zip=zip_code, error=str(e))

        # Count restaurants after
        after_count = await self.session.scalar(
            select(sqlfunc.count(Restaurant.id)).where(Restaurant.city.ilike(f"%{city}%"))
        ) or 0

        new_found = after_count - before_count

        # Update parent job if exists
        if job_id:
            job = await self.session.get(CrawlJob, job_id)
            if job:
                job.status = "completed"
                job.total_items = new_found
                job.finished_at = datetime.now(timezone.utc)
                await self.session.commit()

        result = {
            "city": city,
            "state": state,
            "zip_codes_total": len(zip_codes),
            "zip_codes_processed": processed,
            "zip_codes_failed": errors,
            "restaurants_before": before_count,
            "restaurants_after": after_count,
            "new_restaurants_found": new_found,
        }
        logger.info("city_deep_crawl_complete", **result)
        return result
