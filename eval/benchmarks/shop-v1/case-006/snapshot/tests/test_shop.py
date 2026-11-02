# Happy-path tests only, on purpose: boundaries and error paths are untested,
# so seeded bugs there survive this suite and must be found by the system.
import unittest

from shop.cart import Cart
from shop.pricing import apply_coupon, unit_price


class PricingTest(unittest.TestCase):
    def test_small_order_pays_base_price(self):
        self.assertEqual(unit_price(4.0, 2), 4.0)

    def test_large_order_gets_discount(self):
        self.assertEqual(unit_price(10.0, 20), 9.0)

    def test_no_coupon(self):
        self.assertEqual(apply_coupon(80.0, None), 80.0)

    def test_half_coupon(self):
        self.assertEqual(apply_coupon(80.0, "HALF"), 40.0)


class CartTest(unittest.TestCase):
    def test_total_of_two_items(self):
        cart = Cart()
        cart.add("apple", 1.5, 2)
        cart.add("pear", 2.0)
        self.assertEqual(cart.item_count(), 3)
        self.assertEqual(cart.subtotal(), 5.0)

    def test_remove_everything(self):
        cart = Cart()
        cart.add("apple", 1.5, 2)
        cart.remove("apple", 2)
        self.assertEqual(cart.item_count(), 0)


if __name__ == "__main__":
    unittest.main()
