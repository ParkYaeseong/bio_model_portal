import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.runpod_status import extract_status


class ExtractStatusTests(unittest.TestCase):
    def test_extract_status_from_string(self) -> None:
        status, detail = extract_status({"status": "RUNNING"})
        self.assertEqual(status, "RUNNING")
        self.assertIsNone(detail)

    def test_extract_status_from_dict(self) -> None:
        status, detail = extract_status({"status": {"status": "FAILED", "reason": "boom"}})
        self.assertEqual(status, "FAILED")
        self.assertEqual(detail, "boom")

    def test_extract_status_falls_back_to_state_key(self) -> None:
        status, detail = extract_status({"status": {"state": "COMPLETED"}, "message": "ok"})
        self.assertEqual(status, "COMPLETED")
        self.assertIsNone(detail)


if __name__ == "__main__":
    unittest.main()
