"""Tests for Conversion Intelligence & Neighborhood Penetration Analytics (NIF-148, NIF-149, NIF-150)."""

import uuid

import pytest

from src.db.models import ConversionEvent, ConversionFunnel


# -- NIF-148: ConversionEvent model tests --------------------------------------


class TestConversionEventModel:
    """NIF-148: Conversion event model."""

    def test_event_creation(self):
        event = ConversionEvent(
            restaurant_id=uuid.uuid4(),
            event_type="discovered",
            source="google_crawl",
            metadata_={"campaign": "spring-2026"},
        )
        assert event.event_type == "discovered"
        assert event.source == "google_crawl"
        assert event.metadata_["campaign"] == "spring-2026"

    def test_event_defaults(self):
        event = ConversionEvent(
            restaurant_id=uuid.uuid4(),
            event_type="contacted",
        )
        assert event.lead_id is None
        assert event.source is None

    def test_event_with_lead(self):
        lead_id = uuid.uuid4()
        event = ConversionEvent(
            restaurant_id=uuid.uuid4(),
            lead_id=lead_id,
            event_type="demo_scheduled",
        )
        assert event.lead_id == lead_id

    def test_valid_event_types(self):
        for event_type in ["discovered", "contacted", "demo_scheduled", "pilot_started", "converted", "churned"]:
            event = ConversionEvent(
                restaurant_id=uuid.uuid4(),
                event_type=event_type,
            )
            assert event.event_type == event_type

    def test_table_name(self):
        assert ConversionEvent.__tablename__ == "conversion_events"

    def test_has_restaurant_id_fk(self):
        col = ConversionEvent.__table__.c.restaurant_id
        assert len(col.foreign_keys) == 1

    def test_has_lead_id_fk(self):
        col = ConversionEvent.__table__.c.lead_id
        assert len(col.foreign_keys) == 1

    def test_event_type_indexed(self):
        col = ConversionEvent.__table__.c.event_type
        assert col.index is True

    def test_occurred_at_indexed(self):
        col = ConversionEvent.__table__.c.occurred_at
        assert col.index is True

    def test_restaurant_id_indexed(self):
        col = ConversionEvent.__table__.c.restaurant_id
        assert col.index is True


# -- NIF-149: ConversionFunnel model tests ------------------------------------


class TestConversionFunnelModel:
    """NIF-149: Conversion funnel summary model."""

    def test_funnel_creation(self):
        funnel = ConversionFunnel(
            period="2026-W15",
            zip_code="10001",
            discovered=100,
            contacted=60,
            demo_scheduled=30,
            pilot_started=15,
            converted=10,
            churned=2,
            conversion_rate=10.0,
            avg_days_to_convert=14.5,
        )
        assert funnel.period == "2026-W15"
        assert funnel.zip_code == "10001"
        assert funnel.discovered == 100
        assert funnel.converted == 10
        assert funnel.conversion_rate == 10.0
        assert funnel.avg_days_to_convert == 14.5

    def test_funnel_defaults(self):
        funnel = ConversionFunnel(period="2026-04")
        assert funnel.zip_code is None
        assert funnel.discovered is None or funnel.discovered == 0
        assert funnel.contacted is None or funnel.contacted == 0
        assert funnel.conversion_rate is None or funnel.conversion_rate == 0.0

    def test_table_name(self):
        assert ConversionFunnel.__tablename__ == "conversion_funnels"

    def test_period_indexed(self):
        col = ConversionFunnel.__table__.c.period
        assert col.index is True

    def test_zip_code_indexed(self):
        col = ConversionFunnel.__table__.c.zip_code
        assert col.index is True

    def test_unique_constraint(self):
        constraints = [c.name for c in ConversionFunnel.__table__.constraints if hasattr(c, "name")]
        assert "uq_conversion_funnel_period_zip" in constraints


# -- Service logic tests -------------------------------------------------------


class TestConversionAnalyticsService:
    """Test conversion analytics service functions."""

    def test_valid_event_types_constant(self):
        from src.services.conversion_analytics import VALID_EVENT_TYPES
        assert "discovered" in VALID_EVENT_TYPES
        assert "contacted" in VALID_EVENT_TYPES
        assert "demo_scheduled" in VALID_EVENT_TYPES
        assert "pilot_started" in VALID_EVENT_TYPES
        assert "converted" in VALID_EVENT_TYPES
        assert "churned" in VALID_EVENT_TYPES
        assert len(VALID_EVENT_TYPES) == 6

    def test_invalid_event_type_not_in_set(self):
        from src.services.conversion_analytics import VALID_EVENT_TYPES
        assert "invalid" not in VALID_EVENT_TYPES
        assert "signed_up" not in VALID_EVENT_TYPES


# -- NIF-150: Router tests -----------------------------------------------------


class TestConversionAnalyticsRouter:
    """NIF-150: Conversion analytics API router."""

    def test_router_importable(self):
        from src.api.routers.conversion_analytics import router
        assert router.prefix == "/conversion"

    def test_router_has_create_event_endpoint(self):
        from src.api.routers.conversion_analytics import router
        paths = [r.path for r in router.routes]
        assert "/conversion/events" in paths

    def test_router_has_timeline_endpoint(self):
        from src.api.routers.conversion_analytics import router
        paths = [r.path for r in router.routes]
        assert "/conversion/events/{restaurant_id}" in paths

    def test_router_has_funnel_endpoint(self):
        from src.api.routers.conversion_analytics import router
        paths = [r.path for r in router.routes]
        assert "/conversion/funnel" in paths

    def test_router_has_trends_endpoint(self):
        from src.api.routers.conversion_analytics import router
        paths = [r.path for r in router.routes]
        assert "/conversion/trends" in paths

    def test_router_has_calculate_endpoint(self):
        from src.api.routers.conversion_analytics import router
        paths = [r.path for r in router.routes]
        assert "/conversion/funnel/calculate" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        conversion_paths = [p for p in paths if "conversion" in p]
        assert len(conversion_paths) > 0

    def test_router_tags(self):
        from src.api.routers.conversion_analytics import router
        assert "conversion" in router.tags

    def test_event_to_dict_helper(self):
        from src.api.routers.conversion_analytics import _event_to_dict
        event = ConversionEvent(
            id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
            event_type="discovered",
            source="google",
            metadata_={"test": True},
        )
        d = _event_to_dict(event)
        assert d["event_type"] == "discovered"
        assert d["source"] == "google"
        assert d["metadata"]["test"] is True
        assert d["lead_id"] is None

    def test_event_to_dict_with_lead(self):
        from src.api.routers.conversion_analytics import _event_to_dict
        lead_id = uuid.uuid4()
        event = ConversionEvent(
            id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
            lead_id=lead_id,
            event_type="contacted",
        )
        d = _event_to_dict(event)
        assert d["lead_id"] == str(lead_id)

    def test_funnel_to_dict_helper(self):
        from src.api.routers.conversion_analytics import _funnel_to_dict
        funnel = ConversionFunnel(
            id=uuid.uuid4(),
            period="2026-W15",
            zip_code="10001",
            discovered=100,
            contacted=60,
            demo_scheduled=30,
            pilot_started=15,
            converted=10,
            churned=2,
            conversion_rate=10.0,
            avg_days_to_convert=14.5,
        )
        d = _funnel_to_dict(funnel)
        assert d["period"] == "2026-W15"
        assert d["zip_code"] == "10001"
        assert d["discovered"] == 100
        assert d["converted"] == 10
        assert d["conversion_rate"] == 10.0
        assert d["avg_days_to_convert"] == 14.5
