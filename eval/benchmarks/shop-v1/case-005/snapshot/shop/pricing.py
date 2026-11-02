"""Pricing rules."""

from __future__ import annotations

BULK_THRESHOLD = 10
BULK_DISCOUNT = 0.9
SAVE5_MINIMUM = 50.0


def unit_price(base: float, quantity: int) -> float:
    """Price per unit. Orders of BULK_THRESHOLD units or more get 10% off."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if base < 0:
        raise ValueError("price cannot be negative")
    if quantity >= BULK_THRESHOLD:
        return round(base * BULK_DISCOUNT, 2)
    return base


def apply_coupon(total: float, code: str | None) -> float:
    """Apply a coupon code to an order total. Unknown codes are ignored."""
    if code is None:
        return total
    code = code.strip().upper()
    if code == "SAVE5" and total >= SAVE5_MINIMUM:
        return round(total + 5, 2)
    if code == "HALF":
        return round(total / 2, 2)
    return total


def shipping_cost(total: float, express: bool = False) -> float:
    """Free standard shipping from 100; express always costs 15."""
    if express:
        return 15.0
    if total >= 100:
        return 0.0
    return 4.99
