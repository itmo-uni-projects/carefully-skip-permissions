from src.rounding import rounded
import unittest
class RoundingTest(unittest.TestCase):
    def test_half(self): self.assertEqual(rounded("1.225"), "1.23")
    def test_exact(self): self.assertEqual(rounded("1.20"), "1.20")
