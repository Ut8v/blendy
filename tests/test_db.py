import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.db import MIGRATIONS, Database  # noqa: E402


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(Path(self.tmp) / "t.sqlite")

    def tearDown(self):
        self.db.close()

    def test_migrations_apply_once(self):
        self.assertEqual(self.db.version(), MIGRATIONS[-1][0])
        self.assertEqual(self.db.migrate(), [])

    def test_wal_mode(self):
        self.assertEqual(self.db.one("PRAGMA journal_mode")["journal_mode"], "wal")

    def test_asset_roundtrip_and_index(self):
        self.db.upsert_asset("h1", "polyhaven", "chair", "/x/chair.glb", "CC0", "https://x")
        self.db.index_profile({"hash": "h1", "class": "prop", "profile_version": 1,
                               "landmarks": {"seat": {}, "back": {}}, "flags": {"generated": False}})
        row = self.db.asset_by_ref("polyhaven", "chair")
        self.assertEqual(row["hash"], "h1")
        idx = self.db.one("SELECT * FROM asset_index WHERE hash='h1'")
        self.assertIn("seat", idx["landmark_names"])

    def test_stale_profiles(self):
        self.db.upsert_asset("h1", "local", "a", "/a", None, None)
        self.db.index_profile({"hash": "h1", "class": "prop", "profile_version": 1,
                               "landmarks": {}, "flags": {}})
        self.assertEqual(self.db.stale_profiles(2), ["h1"])
        self.assertEqual(self.db.stale_profiles(1), [])

    def test_recurring_patterns_need_three_distinct_shots(self):
        for i, shot in enumerate(["s01_001", "s01_002", "s01_002"]):
            self.db.insert_incident({"id": f"INC-{i:04d}", "shot": shot, "agent": "lighting",
                                     "category": "judgment", "expected": "e", "observed": "o",
                                     "session": f"sess{i}"})
        self.assertEqual(self.db.recurring_patterns(3), [])
        self.db.insert_incident({"id": "INC-0009", "shot": "s01_003", "agent": "lighting",
                                 "category": "judgment", "expected": "e", "observed": "o",
                                 "session": "sess9"})
        [pat] = self.db.recurring_patterns(3)
        self.assertEqual((pat["agent"], pat["shots"]), ("lighting", 3))

    def test_one_session_counts_once(self):
        for i, shot in enumerate(["s01_001", "s01_002", "s01_003"]):
            self.db.insert_incident({"id": f"INC-{i:04d}", "shot": shot, "agent": "camera",
                                     "category": "judgment", "expected": "e", "observed": "o",
                                     "session": "same"})
        self.assertEqual(self.db.recurring_patterns(3), [])

    def test_shot_state_upsert(self):
        self.db.set_shot_state("s01_001", render_state="rendering", frames_total=48)
        self.db.set_shot_state("s01_001", frames_done=10, last_render=time.time())
        [row] = self.db.shot_states()
        self.assertEqual((row["render_state"], row["frames_done"], row["frames_total"]),
                         ("rendering", 10, 48))

    def test_next_incident_id(self):
        self.assertEqual(self.db.next_incident_id(), "INC-0001")


if __name__ == "__main__":
    unittest.main()
