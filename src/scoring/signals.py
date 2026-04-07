"""ICP signal extractors — v2 aligned to TCT ICP strategy document."""

import math
from datetime import datetime, timezone

from src.utils.logging import get_logger

logger = get_logger("scoring.signals")

# Known national chain restaurants — these are NOT ideal customers
KNOWN_CHAINS = {
    "mcdonald's", "burger king", "wendy's", "taco bell", "subway",
    "domino's", "pizza hut", "papa john's", "kfc", "chick-fil-a",
    "popeyes", "sonic", "arby's", "jack in the box", "panda express",
    "chipotle", "five guys", "panera bread", "olive garden",
    "applebee's", "chili's", "red lobster", "outback steakhouse",
    "denny's", "ihop", "waffle house", "dunkin'", "starbucks",
    "wingstop", "jimmy john's", "jersey mike's", "firehouse subs",
    "sweetgreen", "cava", "shake shack", "in-n-out",
    "el pollo loco", "del taco", "carl's jr", "hardee's",
    "buffalo wild wings", "cracker barrel", "bob evans",
    "golden corral", "texas roadhouse", "longhorn steakhouse",
}

# Modern cloud-based POS systems — preferred for integration
MODERN_POS = {"toast", "square", "clover", "revel", "spoton", "touchbistro"}
LEGACY_POS = {"aloha", "micros", "ncr silver", "heartland"}

POS_PROVIDERS = {
    "toast": "Toast", "square": "Square", "clover": "Clover",
    "lightspeed": "Lightspeed", "aloha": "Aloha (NCR)",
    "micros": "Oracle MICROS", "revel": "Revel Systems",
    "touchbistro": "TouchBistro", "upserve": "Upserve",
    "spoton": "SpotOn", "heartland": "Heartland",
    "ncr silver": "NCR Silver", "shopify": "Shopify POS",
}

# Fine dining / ultra-premium indicators — penalized
FINE_DINING_KEYWORDS = {
    "fine dining", "upscale", "prix fixe", "tasting menu",
    "michelin", "haute cuisine", "omakase",
}


# ── Signal 1: Independence (15%) ─────────────────────────────


def detect_chain(name: str, extracted_data: dict | None = None) -> tuple[bool, str | None]:
    """Check if a restaurant is a known national chain."""
    name_lower = name.lower().strip()
    if not name_lower:
        return False, None

    for chain in KNOWN_CHAINS:
        if chain in name_lower or name_lower in chain:
            return True, chain.title()

    if extracted_data and extracted_data.get("is_chain"):
        return True, extracted_data.get("chain_name")

    return False, None


# ── Signal 2: Platform Dependency (20%) ──────────────────────


def detect_delivery(source_records: list[dict]) -> tuple[bool, list[str], int]:
    """Detect delivery platforms. Returns (has_delivery, platforms, platform_count)."""
    platforms = set()

    for record in source_records:
        source = record.get("source", "")
        if source in ("doordash", "ubereats", "grubhub"):
            platforms.add(source)
        if record.get("has_delivery"):
            dp = record.get("delivery_platform", source)
            if dp:
                platforms.add(dp.lower())

        extracted = record.get("extracted_data", {}) or {}
        for dp in extracted.get("delivery_platforms", []):
            if dp:
                platforms.add(dp.lower())

    # Normalize platform names
    normalized = set()
    for p in platforms:
        if "doordash" in p:
            normalized.add("doordash")
        elif "uber" in p:
            normalized.add("ubereats")
        elif "grubhub" in p:
            normalized.add("grubhub")
        else:
            normalized.add(p)

    return bool(normalized), sorted(normalized), len(normalized)


def platform_dependency_score(platform_count: int) -> float:
    """Score based on number of delivery platforms (more = more commission pain)."""
    if platform_count >= 3:
        return 1.0
    elif platform_count == 2:
        return 0.75
    elif platform_count == 1:
        return 0.5
    return 0.0


# ── Signal 3: POS System (12%) ───────────────────────────────


def detect_pos(raw_text: str = "", extracted_data: dict | None = None) -> tuple[bool, str | None]:
    """Detect POS system from website content or extracted data."""
    text_lower = raw_text.lower()

    for keyword, provider in POS_PROVIDERS.items():
        if keyword in text_lower:
            return True, provider

    if extracted_data:
        if extracted_data.get("has_pos"):
            return True, extracted_data.get("pos_provider")
        for indicator in extracted_data.get("pos_indicators", []):
            ind_lower = indicator.lower()
            for keyword, provider in POS_PROVIDERS.items():
                if keyword in ind_lower:
                    return True, provider

    return False, None


def pos_maturity_score(has_pos: bool, pos_provider: str | None) -> float:
    """Score POS maturity. Modern cloud POS = 1.0, legacy = 0.5, none = 0.3."""
    if not has_pos:
        return 0.3

    if pos_provider:
        provider_lower = pos_provider.lower()
        for modern in MODERN_POS:
            if modern in provider_lower:
                return 1.0
        for legacy in LEGACY_POS:
            if legacy in provider_lower:
                return 0.5

    return 0.7  # Has POS but unknown type


# ── Signal 5: Volume/Revenue Proxy (15%) ─────────────────────


def volume_proxy_score(review_count: int, rating: float) -> float:
    """Estimate order volume from review patterns.

    ICP doc: $500K-$3M revenue, 20+ delivery orders/day.
    Reviews are ~1-2% of orders, so 200+ reviews ≈ 10K-20K orders.
    """
    if review_count <= 0:
        return 0.0
    # Log scale: 50 reviews = 0.4, 200 = 0.7, 500+ = 0.9, 1000+ = 1.0
    volume = min(math.log10(review_count + 1) / 3.0, 1.0)
    # Slight boost for high-rated (indicates quality + repeat customers)
    quality_bonus = 0.1 if rating and rating >= 4.0 else 0.0
    return min(volume + quality_bonus, 1.0)


# ── Signal 6: Cuisine/Category Fit (10%) ─────────────────────


def cuisine_fit_score(cuisine_types: list[str], price_tier: str | None = None) -> float:
    """Score cuisine fit. Penalize fine dining/ultra-premium. All others = 1.0.

    ICP doc: ❌ Ultra-Fine Dining — low delivery frequency.
    """
    if not cuisine_types:
        return 0.8  # Unknown = neutral-positive

    cuisines_lower = " ".join(c.lower() for c in cuisine_types)

    # Check for fine dining indicators
    for keyword in FINE_DINING_KEYWORDS:
        if keyword in cuisines_lower:
            return 0.2

    # $$$$ price tier = likely fine dining
    if price_tier and price_tier.count("$") >= 4:
        return 0.2

    return 1.0


# ── Signal 7: Price Point Fit (8%) ───────────────────────────


def price_point_score(price_tier: str | None) -> float:
    """Score based on price tier. ICP doc: Avg ticket $15-$40 ideal.

    $ = budget (~$10-15) = 0.7
    $$ = moderate (~$15-40) = 1.0 (ideal)
    $$$ = upscale (~$40-80) = 0.5
    $$$$ = fine dining (~$80+) = 0.1
    """
    if not price_tier:
        return 0.7  # Unknown = assume moderate

    dollar_count = price_tier.count("$")
    if dollar_count == 1:
        return 0.7
    elif dollar_count == 2:
        return 1.0
    elif dollar_count == 3:
        return 0.5
    elif dollar_count >= 4:
        return 0.1
    return 0.7


# ── Signal 8: Engagement/Recency (8%) ────────────────────────


def engagement_recency_score(latest_review_date: datetime | None = None) -> float:
    """Score based on recency of activity. Recent activity = active business.

    ICP doc: "Strong neighborhood presence", "Repeat customer base".
    """
    if not latest_review_date:
        return 0.3  # Unknown = low confidence

    now = datetime.now(timezone.utc)
    if latest_review_date.tzinfo is None:
        latest_review_date = latest_review_date.replace(tzinfo=timezone.utc)

    days_ago = (now - latest_review_date).days

    if days_ago <= 30:
        return 1.0
    elif days_ago <= 90:
        return 0.7
    elif days_ago <= 180:
        return 0.4
    return 0.1


# ── Disqualifiers ────────────────────────────────────────────


def compute_disqualifier_penalty(
    is_national_chain: bool,
    is_fine_dining: bool,
    has_any_delivery: bool,
    review_count: int,
) -> float:
    """Compute negative penalty points. Applied after weighted score.

    ICP doc: ❌ National Chains, ❌ Ultra-Fine Dining, ❌ No delivery discipline.
    """
    penalty = 0.0
    if is_national_chain:
        penalty += 30.0
    if is_fine_dining:
        penalty += 15.0
    if not has_any_delivery:
        penalty += 20.0
    if review_count < 10:
        penalty += 10.0
    return penalty


# ── Legacy compat ────────────────────────────────────────────


def normalize_review_signal(review_count: int, rating: float) -> float:
    """Legacy review signal — kept for backward compatibility."""
    return volume_proxy_score(review_count, rating)
