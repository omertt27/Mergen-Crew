import unittest

from textutil.format import paginate, truncate


class FormatTest(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(paginate(list(range(25)), 1), list(range(10)))

    def test_truncate_long_text(self):
        self.assertEqual(truncate("hello world", 6), "hello…")


if __name__ == "__main__":
    unittest.main()
