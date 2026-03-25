.PHONY: up down migrate test lint run worker beat

up:
	docker compose up -d

down:
	docker compose down

run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A src.tasks.celery_app worker --loglevel=info

beat:
	celery -A src.tasks.celery_app beat --loglevel=info

migrate:
	alembic upgrade head

migration:
	alembic revision --autogenerate -m "$(msg)"

test:
	pytest tests/ -v --cov=src

lint:
	ruff check src/ tests/
