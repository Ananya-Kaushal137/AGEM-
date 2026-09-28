"""Pydantic request/response schemas. Every schema inherits `StrictModel` (FR-API-002)."""

from .base import StrictModel

__all__ = ["StrictModel"]
