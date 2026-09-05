from src.slug import slug
import unittest
class SlugTest(unittest.TestCase):
    def test_spaces(self): self.assertEqual(slug(" Alpha\u00a0 Beta  "), "alpha-beta")
    def test_empty(self): self.assertEqual(slug(" "), "")
