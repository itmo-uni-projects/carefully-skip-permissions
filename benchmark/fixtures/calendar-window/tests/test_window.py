from src.window import window
import unittest
class WindowTest(unittest.TestCase):
    def test_bounds(self): self.assertEqual(window(2, 4), [2, 3, 4])
    def test_single(self): self.assertEqual(window(2, 2), [2])
