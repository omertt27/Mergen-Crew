"""Shopping cart."""

from __future__ import annotations

from .pricing import apply_coupon, shipping_cost, unit_price


class Cart:
    def __init__(self, max_items: int = 100):
        self.max_items = max_items
        self.lines: dict[str, tuple[float, int]] = {}

    def add(self, sku: str, price: float, quantity: int = 1) -> None:
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        if self.item_count() + quantity > self.max_items:
            raise ValueError("cart is full")
        if sku in self.lines:
            existing_price, existing_qty = self.lines[sku]
            self.lines[sku] = (existing_price, existing_qty + quantity)
        else:
            self.lines[sku] = (price, quantity)

    def remove(self, sku: str, quantity: int = 1) -> None:
        if sku not in self.lines:
            raise KeyError(sku)
        price, qty = self.lines[sku]
        if quantity >= qty:
            del self.lines[sku]
        else:
            self.lines[sku] = (price, qty - quantity)

    def item_count(self) -> int:
        return sum(qty for _, qty in self.lines.values())

    def subtotal(self) -> float:
        return round(sum(unit_price(price, qty) * qty for price, qty in self.lines.values()), 2)

    def total(self, coupon: str | None = None, express: bool = False) -> float:
        discounted = apply_coupon(self.subtotal(), coupon)
        return round(discounted + shipping_cost(discounted, express), 2)
