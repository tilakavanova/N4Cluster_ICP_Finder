# N4Cluster ICP Finder

Production-grade web crawling and business intelligence system for identifying and scoring local restaurants that match a defined **Ideal Customer Profile (ICP)**: independent restaurants, delivery-enabled, POS-enabled, in dense neighborhoods.

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  FastAPI     │    │  Celery      │    │  Celery     │
│  REST API    │    │  Workers     │    │  Beat       │
│  :8000       │    │  (crawl/     │    │  (scheduler)│
│              │    │   extract/   │    │             │
│              │    │   score)     │    │             │
└──────┬───────┘    └──────┬───────┘    └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌──────────────┐
│ PostgreSQL  │    │    Redis     │
│ (PostGIS)   │    │   (broker)   │
└─────────────┘    └──────────────┘
```

## Features

- **Multi-source Crawling**: Google Maps, Yelp, DoorDash, UberEats, restaurant websites
- **LLM Extraction**: OpenAI / Claude for structured data extraction with fallback
- **ICP Scoring Engine**: Weighted scoring (independence, delivery, POS, geo-density, reviews)
- **Geo-Density Clustering**: HDBSCAN-based neighborhood density analysis
- **REST API**: Search, filter, leaderboard, CSV/JSON export
- **Task Queue**: Celery + Redis for async crawl/extract/score pipelines
- **Scheduler**: Daily incremental crawls, weekly full re-scoring

## Tech Stack

| Component     | Technology                    |
|---------------|-------------------------------|
| Backend       | Python 3.12 + FastAPI         |
| Crawling      | Playwright + httpx            |
| Queue         | Celery + Redis                |
| Database      | PostgreSQL (PostGIS)          |
| ORM           | SQLAlchemy 2.0 (async)        |
| Migrations    | Alembic                       |
| LLM           | OpenAI / Anthropic Claude     |
| Clustering    | scikit-learn + HDBSCAN        |
| Logging       | structlog (JSON)              |

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd N4Cluster_ICP_Finder
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start with Docker

```bash
docker compose up -d
```

This starts: PostgreSQL, Redis, FastAPI app, Celery worker, Celery beat.

### 3. Run migrations

```bash
docker compose exec app alembic upgrade head
```

### 4. Access the API

- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[dev]"
playwright install chromium

# Start services (Postgres + Redis)
docker compose up postgres redis -d

# Run migrations
alembic upgrade head

# Start the API server
make run

# Start Celery worker (separate terminal)
make worker

# Start Celery beat (separate terminal)
make beat
```

## API Endpoints

### Restaurants

| Method | Endpoint                          | Description                    |
|--------|-----------------------------------|--------------------------------|
| GET    | `/api/v1/restaurants`             | List with filters              |
| GET    | `/api/v1/restaurants/search?q=`   | Full-text search               |
| GET    | `/api/v1/restaurants/{id}`        | Full details + sources + score |

### Crawl Jobs

| Method | Endpoint                | Description              |
|--------|-------------------------|--------------------------|
| POST   | `/api/v1/jobs`          | Create crawl job         |
| GET    | `/api/v1/jobs`          | List jobs                |
| GET    | `/api/v1/jobs/{id}`     | Job status               |

### ICP Scores

| Method | Endpoint                       | Description                  |
|--------|--------------------------------|------------------------------|
| GET    | `/api/v1/scores/leaderboard`   | Top restaurants by ICP score |
| POST   | `/api/v1/scores/recalculate`   | Trigger re-scoring           |
| GET    | `/api/v1/scores/export`        | Export leads (CSV/JSON)      |

### Example: Create a crawl job

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"source": "google_maps", "query": "restaurants", "location": "New York, NY"}'
```

### Example: Export high-scoring leads

```bash
curl "http://localhost:8000/api/v1/scores/export?format=csv&min_score=60&fit_label=excellent"
```

## ICP Scoring Model

| Signal          | Weight | Description                           |
|-----------------|--------|---------------------------------------|
| Independent     | 30%    | Not a known chain/franchise           |
| Delivery        | 25%    | Available on DoorDash/UberEats        |
| POS Enabled     | 20%    | Uses Toast, Square, Clover, etc.      |
| Geo Density     | 15%    | Located in dense restaurant area      |
| Review Signal   | 10%    | Review volume + rating (log-scaled)   |

**Fit Labels**: Excellent (75+), Good (55-74), Moderate (35-54), Poor (<35)

## Database Schema

4 tables: `restaurants`, `source_records`, `icp_scores`, `crawl_jobs`

- Deduplication via `UNIQUE(name, address)` with upsert
- Per-source provenance in `source_records` (JSONB raw + extracted)
- Scoring versioning for historical tracking

## Deployment

### Railway / Render

1. Connect your GitHub repo
2. Set environment variables from `.env.example`
3. Add PostgreSQL and Redis addons
4. Deploy — the Dockerfile handles everything

### AWS (ECS / EC2)

1. Build: `docker build -t icp-finder .`
2. Push to ECR
3. Deploy with ECS using `docker-compose.yml` as reference
4. Use RDS for PostgreSQL, ElastiCache for Redis

### Environment Variables

| Variable            | Required | Description                |
|---------------------|----------|----------------------------|
| `DATABASE_URL`      | Yes      | PostgreSQL connection URL  |
| `REDIS_URL`         | Yes      | Redis connection URL       |
| `OPENAI_API_KEY`    | No*      | OpenAI API key             |
| `ANTHROPIC_API_KEY` | No*      | Anthropic API key          |
| `PROXY_LIST`        | No       | Comma-separated proxy URLs |
| `SECRET_KEY`        | Yes      | Application secret         |
| `LOG_LEVEL`         | No       | Default: INFO              |

*At least one LLM API key is required for extraction.

## Testing

```bash
make test
```

## Project Structure

```
src/
├── main.py              # FastAPI app
├── config.py            # Pydantic settings
├── db/
│   ├── models.py        # ORM models
│   └── session.py       # Async DB session
├── crawlers/
│   ├── base.py          # Abstract crawler (retry, rate-limit)
│   ├── google_maps.py   # Google Maps (Playwright)
│   ├── yelp.py          # Yelp (httpx)
│   ├── delivery.py      # DoorDash + UberEats (Playwright)
│   └── website.py       # Generic website scraper
├── extraction/
│   ├── llm_client.py    # OpenAI/Claude with fallback
│   ├── prompts.py       # Extraction prompt templates
│   └── extractor.py     # Extraction pipeline
├── scoring/
│   ├── signals.py       # Chain/POS/delivery detection
│   ├── geo_density.py   # HDBSCAN geo clustering
│   └── icp_scorer.py    # Weighted composite scorer
├── tasks/
│   ├── celery_app.py    # Celery config + beat schedule
│   ├── crawl_tasks.py   # Crawl task + daily scheduler
│   ├── extract_tasks.py # LLM extraction task
│   └── score_tasks.py   # Scoring task
└── api/
    ├── schemas.py       # Pydantic models
    └── routers/
        ├── restaurants.py
        ├── jobs.py
        └── scores.py
```

## License

MIT
