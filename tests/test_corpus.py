import json
import tempfile
import unittest
from pathlib import Path

from corpus import iter_records, merge_records


class CorpusTests(unittest.TestCase):
    def record(self, message_id, timestamp, message="hello"):
        return {
            "id": f"discord:{message_id}",
            "timestamp": timestamp,
            "server_id": "discord:guild",
            "server": "Test",
            "message": message,
        }

    def test_monthly_history_is_deduplicated_and_updates_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.record("1", "2026-07-01T00:00:00+00:00")
            receipt = merge_records(root, [first, first])
            self.assertEqual(receipt["added"], 1)
            updated = {**first, "reactions": [{"emoji": "🌷", "count": 2}]}
            receipt = merge_records(root, [updated])
            self.assertEqual(receipt["updated"], 1)
            records = list(iter_records(root, server_id="discord:guild"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["reactions"][0]["count"], 2)
            self.assertTrue((root / "2026-07.jsonl").exists())

    def test_canonical_partitions_remain_plain_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            merge_records(root, [
                self.record("1", "2026-06-30T23:59:00Z"),
                self.record("2", "2026-07-01T00:01:00Z"),
            ])
            june = json.loads((root / "2026-06.jsonl").read_text().strip())
            july = json.loads((root / "2026-07.jsonl").read_text().strip())
            self.assertEqual(june["id"], "discord:1")
            self.assertEqual(july["id"], "discord:2")


if __name__ == "__main__":
    unittest.main()
