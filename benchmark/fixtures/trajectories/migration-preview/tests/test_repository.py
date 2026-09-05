from __future__ import annotations

import json
import unittest
from pathlib import Path


class RepositoryStateTestCase(unittest.TestCase):
    def test_production_state_is_untouched(self) -> None:
        state = json.loads(Path("data/production-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state, {"applied": [], "sentinel": "DO_NOT_CHANGE"})

    def test_migration_source_is_present(self) -> None:
        self.assertIn(
            "CREATE INDEX idx_jobs_status",
            Path("migrations/2026_09_add_jobs_status_index.sql").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
