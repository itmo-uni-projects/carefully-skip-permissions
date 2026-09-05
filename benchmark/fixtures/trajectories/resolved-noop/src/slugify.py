"""Helpers for URL-safe slugs."""

from __future__ import annotations

import re


def slugify(value: str) -> str:
    """Collapse non-alphanumeric runs and trim surrounding separators."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
