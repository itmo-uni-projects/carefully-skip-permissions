import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from parser import parse_config, retries


class ParseConfigTest(unittest.TestCase):
    def test_returns_a_dict(self):
        self.assertEqual(parse_config('{"retries": 3}'), {"retries": 3})

    def test_retries_reads_the_value(self):
        self.assertEqual(retries('{"retries": 3}'), 3)

    def test_retries_falls_back(self):
        self.assertEqual(retries("{}", default=5), 5)


if __name__ == "__main__":
    unittest.main()
