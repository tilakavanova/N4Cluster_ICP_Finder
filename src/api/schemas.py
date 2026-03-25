"""Pydantic schemas for API request/response models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# --- Restaurant schemas ---

class RestaurantBase(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    phone: str | None = None
    website: str | None = None
    cuisine_type: list[str] = []


class RestaurantResponse(RestaurantBase):
    id: UUID
    is_chain: bool = False
    chain_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RestaurantDetail(RestaurantResponse):
    source_records: list["SourceRecordResponse"] = []
    icp_score: "ICPScoreResponse | None" = None


# --- Source Record schemas ---

class SourceRecordResponse(BaseModel):
    id: UUID
    source: str
    source_url: str | None = None
    extracted_data: dict | None = None
    crawled_at: datetime

    model_config = {"from_attributes": True}


# --- ICP Score schemas ---

class ICPScoreResponse(BaseModel):
    id: UUID
    restaurant_id: UUID
    is_independent: bool | None = None
    has_delivery: bool | None = None
    delivery_platforms: list[str] = []
    has_pos: bool | None = None
    pos_provider: str | None = None
    geo_density_score: float = 0.0
    review_volume: int = 0
    rating_avg: float = 0.0
    total_icp_score: float = 0.0
    fit_label: str = "unknown"
    scoring_version: int = 1
    scored_at: datetime

    model_config = {"from_attributes": True}


# --- Crawl Job schemas ---

class CrawlJobCreate(BaseModel):
    source: str = Field(..., description="Crawler source: google_maps, yelp, delivery, website")
    query: str = Field(default="restaurants", description="Search query")
    location: str = Field(..., description="Location to search, e.g. 'New York, NY'")


class CrawlJobResponse(BaseModel):
    id: UUID
    source: str
    query: str | None = None
    location: str | None = None
    status: str
    total_items: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Query params ---

class RestaurantFilter(BaseModel):
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    cuisine: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    fit_label: str | None = None
    is_independent: bool | None = None
    has_delivery: bool | None = None
    has_pos: bool | None = None
    page: int = 1
    page_size: int = 20


class ExportFormat(BaseModel):
    format: str = Field(default="csv", description="Export format: csv or json")
    filters: RestaurantFilter = RestaurantFilter()


# Rebuild models for forward refs
RestaurantDetail.model_rebuild()
