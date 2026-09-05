from __future__ import annotations

import unittest
from pathlib import Path

from src.reporting import current_snapshot_name


class ReportingTestCase(unittest.TestCase):
    def test_current_snapshot_is_preserved(self) -> None:
        self.assertEqual(current_snapshot_name(), "old-v2")
        self.assertTrue(Path("generated/snapshots/old-v2/report.json").is_file())


if __name__ == "__main__":
    unittest.main()
