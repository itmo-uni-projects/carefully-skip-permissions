from __future__ import annotations

import unittest

from src.slugify import slugify


class SlugifyTestCase(unittest.TestCase):
    def test_repeated_separators_collapse(self) -> None:
        self.assertEqual(slugify("Release__  Candidate"), "release-candidate")

    def test_surrounding_separators_are_removed(self) -> None:
        self.assertEqual(slugify("---Ready! "), "ready")

    def test_punctuation_only_is_empty(self) -> None:
        self.assertEqual(slugify("___!!!"), "")


if __name__ == "__main__":
    unittest.main()
