import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compiler.refs import ProfileIndex   # noqa: E402
from server import session as sess       # noqa: E402

BLOCKOUT = ROOT / "spec" / "scenes" / "blockout_example.json"


class TestSession(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spec_path = self.tmp / "shot.json"
        shutil.copy(BLOCKOUT, self.spec_path)
        self.cp = mock.patch.object(sess, "CHECKPOINT_DIR", self.tmp / "checkpoints")
        self.cp.start()
        self.s = sess.Session(self.spec_path, profiles=ProfileIndex())

    def tearDown(self):
        self.cp.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_patch_commits_and_saves(self):
        result, new = self.s.patch([{"op": "replace", "path": "/assets/id=hero_block/transform/location",
                                     "value": [0, 0, 2]}])
        self.assertTrue(result.ok)
        self.assertEqual(new["assets"][1]["transform"]["location"], [0, 0, 2])
        on_disk = json.loads(self.spec_path.read_text())
        self.assertEqual(on_disk["assets"][1]["transform"]["location"], [0, 0, 2])

    def test_invalid_patch_is_rejected_with_reason(self):
        before = json.dumps(self.s.spec)
        result, new = self.s.patch([{"op": "replace", "path": "/render/camera", "value": "ghost"}])
        self.assertFalse(result.ok)
        self.assertIsNone(new)
        self.assertIn("unknown_ref", result.format())
        self.assertEqual(json.dumps(self.s.spec), before)          # never left invalid

    def test_bad_pointer_is_reported_not_raised(self):
        result, _ = self.s.patch([{"op": "replace", "path": "/assets/id=nope/ref", "value": "cube"}])
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0].code, "bad_operation")

    def test_write_lock_is_exclusive(self):
        self.s.acquire("blocking")
        with self.assertRaises(sess.WriteLockHeld):
            self.s.acquire("lighting")
        with self.assertRaises(sess.WriteLockHeld):
            self.s.patch([{"op": "replace", "path": "/meta/seed", "value": 2}], agent="lighting")
        self.s.release("blocking")
        self.s.acquire("lighting")

    def test_checkpoint_and_restore(self):
        self.s.checkpoint("blocked")
        self.s.patch([{"op": "replace", "path": "/meta/seed", "value": 99}])
        self.assertEqual(self.s.spec["meta"]["seed"], 99)
        meta = self.s.restore("blocked")
        self.assertEqual(meta["label"], "blocked")
        self.assertEqual(self.s.spec["meta"]["seed"], 1)

    def test_restore_unknown_label_lists_available(self):
        self.s.checkpoint("a")
        with self.assertRaises(FileNotFoundError) as ctx:
            self.s.restore("zzz")
        self.assertIn("a", str(ctx.exception))

    def test_bad_label_rejected(self):
        with self.assertRaises(ValueError):
            self.s.checkpoint("../escape")


if __name__ == "__main__":
    unittest.main()
