import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The packaging step puts dist/ ahead of src/ on the path, so a stale build
# silently shadows the real sources. That is the bug under test.
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "dist"))

from app import version  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_reports_the_source_version(self):
        self.assertEqual(version(), "2.0.0")


if __name__ == "__main__":
    unittest.main()
