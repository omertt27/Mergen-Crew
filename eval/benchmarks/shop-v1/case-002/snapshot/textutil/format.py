"""Formatting helpers for listings."""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


def paginate(items: Sequence[T], page: int, per_page: int = 10) -> list[T]:
    """Return one page of items; pages are numbered from 1."""
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be positive")
    start = (page - 1) * per_page
    return list(items[start : start + per_page])


def page_count(total: int, per_page: int = 10) -> int:
    if per_page < 1:
        raise ValueError("per_page must be positive")
    if total == 0:
        return 1
    return (total + per_page - 1) // per_page


def truncate(text: str, width: int) -> str:
    """Shorten text to at most `width` characters, ending with an ellipsis if cut."""
    if width < 1:
        raise ValueError("width must be positive")
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"
