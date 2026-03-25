"""Individual ICP signal extractors."""

import math

from src.utils.logging import get_logger

logger = get_logger("scoring.signals")

# Known chain restaurants (subset — extend as needed)
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

# Known POS providers
POS_PROVIDERS = {
    "toast": "Toast",
    "square": "Square",
    "clover": "Clover",
    "lightspeed": "Lightspeed",
    "aloha": "Aloha (NCR)",
    "micros": "Oracle MICROS",
    "revel": "Revel Systems",
    "touchbistro": "TouchBistro",
    "upserve": "Upserve",
    "spoton": "SpotOn",
    "heartland": "Heartland",
    "ncr silver": "NCR Silver",
    "shopify": "Shopify POS",
}


def detect_chain(name: str, extracted_data: dict | None = None) -> tuple[bool, str | None]:
    """Check if a restaurant is part of a known chain."""
    name_lower = name.lower().strip()

    for chain in KNOWN_CHAINS:
        if chain in name_lower or name_lower in chain:
            logger.info("chain_detected", name=name, chain=chain)
            return True, chain.title()

    if extracted_data and extracted_data.get("is_chain"):
        return True, extracted_data.get("chain_name")

    return False, None


def detect_pos(raw_text: str = "", extracted_data: dict | None = None) -> tuple[bool, str | None]:
    """Detect POS system from website content or extracted data."""
    text_lower = raw_text.lower()

    for keyword, provider in POS_PROVIDERS.items():
        if keyword in text_lower:
            logger.info("pos_detected", provider=provider)
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


def detect_delivery(source_records: list[dict]) -> tuple[bool, list[str]]:
    """Detect delivery availability from source records."""
    platforms = set()

    for record in source_records:
        source = record.get("source", "")
        if source in ("doordash", "ubereats"):
            platforms.add(source)
        if record.get("has_delivery"):
            if record.get("delivery_platform"):
                platforms.add(record["delivery_platform"])
            else:
                platforms.add(source)

        extracted = record.get("extracted_data", {}) or {}
        for dp in extracted.get("delivery_platforms", []):
            if dp:
                platforms.add(dp.lower())

    return bool(platforms), list(platforms)


def normalize_review_signal(review_count: int, rating: float) -> float:
    """Normalize review volume and rating into a 0-1 signal."""
    volume_score = min(math.log10(max(review_count, 1) + 1) / 3.0, 1.0)
    rating_score = max(0, (rating - 1)) / 4.0 if rating else 0.0
    return 0.7 * volume_score + 0.3 * rating_score
