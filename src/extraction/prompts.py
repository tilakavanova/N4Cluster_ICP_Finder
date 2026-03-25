"""Prompt templates for LLM-based data extraction."""

RESTAURANT_EXTRACTION_PROMPT = """You are a data extraction specialist. Given the following text from a restaurant's web presence, extract structured information.

TEXT:
{text}

Extract the following fields as JSON. Use null for fields you cannot determine:
{{
    "name": "restaurant name",
    "address": "full street address",
    "city": "city name",
    "state": "2-letter state code",
    "zip_code": "ZIP code",
    "phone": "phone number",
    "cuisine_type": ["list", "of", "cuisines"],
    "is_chain": false,
    "chain_name": null,
    "has_online_ordering": false,
    "pos_indicators": ["any POS system mentions like Toast, Square, Clover, etc."],
    "delivery_platforms": ["doordash", "ubereats", "grubhub"],
    "menu_size_estimate": "small/medium/large",
    "hours_of_operation": "business hours if found",
    "social_media": ["list of social media URLs"]
}}

Return ONLY valid JSON, no markdown fencing."""


CHAIN_DETECTION_PROMPT = """Determine if the following restaurant is part of a chain or franchise, or if it is an independent restaurant.

Restaurant Name: {name}
Address: {address}
Additional Context: {context}

Respond with JSON:
{{
    "is_chain": true/false,
    "confidence": 0.0-1.0,
    "chain_name": "name of chain if applicable, null otherwise",
    "reasoning": "brief explanation"
}}

Return ONLY valid JSON."""


POS_DETECTION_PROMPT = """Analyze the following restaurant website text for any indicators of Point-of-Sale (POS) systems or online ordering platforms being used.

TEXT:
{text}

Look for mentions of: Toast, Square, Clover, Lightspeed, Aloha, Micros, Revel, Shopify POS, TouchBistro, Upserve, SpotOn, Heartland, NCR Silver.

Also look for: online ordering widgets, embedded ordering iframes, checkout systems.

Respond with JSON:
{{
    "has_pos": true/false,
    "pos_provider": "provider name or null",
    "has_online_ordering": true/false,
    "ordering_platform": "platform name or null",
    "confidence": 0.0-1.0,
    "evidence": ["list of text snippets that indicate POS/ordering"]
}}

Return ONLY valid JSON."""
