"""HubSpot Free CRM integration service.

Syncs leads to HubSpot contacts and deals via REST API v3.
"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings
from src.db.models import Lead
from src.utils.logging import get_logger

logger = get_logger("hubspot")

HUBSPOT_API_BASE = "https://api.hubapi.com"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.hubspot_api_key}",
        "Content-Type": "application/json",
    }


def _deal_stage_from_fit(fit_label: str | None) -> str:
    """Map ICP fit label to HubSpot deal stage ID."""
    mapping = {
        "excellent": "qualifiedtobuy",
        "good": "presentationscheduled",
        "moderate": "appointmentscheduled",
        "poor": "appointmentscheduled",
    }
    return mapping.get(fit_label or "", "appointmentscheduled")


class HubSpotService:
    """Sync leads to HubSpot Free CRM."""

    def __init__(self):
        self.enabled = bool(settings.hubspot_api_key)

    async def sync_lead(self, lead: Lead) -> dict | None:
        """Create or update a HubSpot contact and deal for a lead.

        Returns dict with hubspot_contact_id and hubspot_deal_id, or None if disabled.
        """
        if not self.enabled:
            logger.debug("hubspot_disabled")
            return None

        contact_id = await self._upsert_contact(lead)
        if not contact_id:
            return None

        deal_id = await self._create_deal(lead, contact_id)

        result = {
            "hubspot_contact_id": contact_id,
            "hubspot_deal_id": deal_id,
        }
        logger.info(
            "hubspot_sync_complete",
            lead_id=str(lead.id),
            contact_id=contact_id,
            deal_id=deal_id,
        )
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _upsert_contact(self, lead: Lead) -> str | None:
        """Create or update HubSpot contact. Returns contact ID."""
        properties = {
            "email": lead.email,
            "firstname": lead.first_name,
            "lastname": lead.last_name,
            "company": lead.company or "",
            "phone": "",
        }

        # Add ICP enrichment as custom properties
        if lead.icp_fit_label:
            properties["icp_fit_label"] = lead.icp_fit_label
        if lead.icp_total_score is not None:
            properties["icp_score"] = str(lead.icp_total_score)
        if lead.is_independent is not None:
            properties["is_independent"] = str(lead.is_independent).lower()
        if lead.has_delivery is not None:
            properties["has_delivery"] = str(lead.has_delivery).lower()
        if lead.matched_restaurant_name:
            properties["matched_restaurant"] = lead.matched_restaurant_name
        if lead.delivery_platforms:
            properties["delivery_platforms"] = ", ".join(lead.delivery_platforms)
        if lead.pos_provider:
            properties["pos_provider"] = lead.pos_provider

        # UTM tracking
        if lead.utm_source:
            properties["hs_analytics_source"] = lead.utm_source
        if lead.utm_campaign:
            properties["utm_campaign"] = lead.utm_campaign

        async with httpx.AsyncClient(timeout=15) as client:
            # Try to find existing contact by email
            search_resp = await client.post(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/search",
                headers=_headers(),
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": lead.email,
                        }]
                    }]
                },
            )

            if search_resp.status_code == 200 and search_resp.json().get("total", 0) > 0:
                # Update existing contact
                contact_id = search_resp.json()["results"][0]["id"]
                update_resp = await client.patch(
                    f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{contact_id}",
                    headers=_headers(),
                    json={"properties": properties},
                )
                if update_resp.status_code == 200:
                    logger.info("hubspot_contact_updated", contact_id=contact_id)
                    return contact_id
                logger.error("hubspot_contact_update_failed", status=update_resp.status_code, body=update_resp.text)
                return None

            # Create new contact
            create_resp = await client.post(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts",
                headers=_headers(),
                json={"properties": properties},
            )
            if create_resp.status_code == 201:
                contact_id = create_resp.json()["id"]
                logger.info("hubspot_contact_created", contact_id=contact_id)
                return contact_id

            logger.error("hubspot_contact_create_failed", status=create_resp.status_code, body=create_resp.text)
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _create_deal(self, lead: Lead, contact_id: str) -> str | None:
        """Create HubSpot deal linked to contact. Returns deal ID."""
        stage = _deal_stage_from_fit(lead.icp_fit_label)
        properties: dict[str, str] = {
            "dealname": f"{lead.company or lead.first_name} - {lead.icp_fit_label or 'new'} lead",
            "dealstage": stage,
            "description": f"Source: {lead.source}",
        }
        if settings.hubspot_pipeline_id:
            properties["pipeline"] = settings.hubspot_pipeline_id

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/deals",
                headers=_headers(),
                json={
                    "properties": properties,
                    "associations": [{
                        "to": {"id": contact_id},
                        "types": [{
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 3,  # deal-to-contact
                        }]
                    }],
                },
            )
            if resp.status_code == 201:
                deal_id = resp.json()["id"]
                logger.info("hubspot_deal_created", deal_id=deal_id, stage=stage)
                return deal_id

            logger.error("hubspot_deal_create_failed", status=resp.status_code, body=resp.text)
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_deal_by_id(self, deal_id: str) -> dict | None:
        """Fetch a deal's properties from HubSpot by deal ID.

        Args:
            deal_id: HubSpot deal object ID.

        Returns:
            Dict of deal properties, or None on failure.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/{deal_id}",
                headers=_headers(),
                params={"properties": "dealname,dealstage,closedate,pipeline"},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error("hubspot_get_deal_failed", deal_id=deal_id, status=resp.status_code)
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_contact_by_id(self, contact_id: str) -> dict | None:
        """Fetch a contact's properties from HubSpot by contact ID.

        Args:
            contact_id: HubSpot contact object ID.

        Returns:
            Dict of contact properties, or None on failure.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{contact_id}",
                headers=_headers(),
                params={"properties": "email,firstname,lastname,company,phone"},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error("hubspot_get_contact_failed", contact_id=contact_id, status=resp.status_code)
            return None
