"""Shared FastAPI dependencies."""

from src.db.session import get_session

__all__ = ["get_session"]
