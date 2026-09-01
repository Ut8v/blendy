import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.patch import PatchError, apply_patch, get  # noqa: E402


def doc():
    return {"a": 1, "list": [{"id": "x", "v": 1}, {"id": "y", "v": 2}], "obj": {"k": "v"}}


class TestPatch(unittest.TestCase):
    def test_add_replace_remove(self):
        out = apply_patch(doc(), [{"op": "add", "path": "/b", "value": 2},
                                  {"op": "replace", "path": "/a", "value": 9},
                                  {"op": "remove", "path": "/obj/k"}])
        self.assertEqual(out["b"], 2)
        self.assertEqual(out["a"], 9)
        self.assertEqual(out["obj"], {})

    def test_original_is_untouched(self):
        d = doc()
        apply_patch(d, [{"op": "replace", "path": "/a", "value": 9}])
        self.assertEqual(d["a"], 1)

    def test_address_array_element_by_id(self):
        out = apply_patch(doc(), [{"op": "replace", "path": "/list/id=y/v", "value": 42}])
        self.assertEqual(out["list"][1]["v"], 42)

    def test_unknown_id_is_a_patch_error(self):
        with self.assertRaises(PatchError) as ctx:
            apply_patch(doc(), [{"op": "replace", "path": "/list/id=z/v", "value": 1}])
        self.assertIn("no element with id 'z'", str(ctx.exception))

    def test_append_with_dash(self):
        out = apply_patch(doc(), [{"op": "add", "path": "/list/-", "value": {"id": "z"}}])
        self.assertEqual(out["list"][2]["id"], "z")

    def test_insert_at_index(self):
        out = apply_patch(doc(), [{"op": "add", "path": "/list/0", "value": {"id": "w"}}])
        self.assertEqual([e["id"] for e in out["list"]], ["w", "x", "y"])

    def test_move_and_copy(self):
        out = apply_patch(doc(), [{"op": "move", "from": "/list/id=x", "path": "/list/-"},
                                  {"op": "copy", "from": "/obj", "path": "/obj2"}])
        self.assertEqual([e["id"] for e in out["list"]], ["y", "x"])
        self.assertEqual(out["obj2"], {"k": "v"})

    def test_test_op(self):
        apply_patch(doc(), [{"op": "test", "path": "/a", "value": 1}])
        with self.assertRaises(PatchError):
            apply_patch(doc(), [{"op": "test", "path": "/a", "value": 2}])

    def test_replace_missing_key_fails(self):
        with self.assertRaises(PatchError):
            apply_patch(doc(), [{"op": "replace", "path": "/nope", "value": 1}])

    def test_failure_names_operation_index(self):
        with self.assertRaises(PatchError) as ctx:
            apply_patch(doc(), [{"op": "replace", "path": "/a", "value": 2},
                                {"op": "remove", "path": "/missing"}])
        self.assertEqual(ctx.exception.index, 1)

    def test_escaped_pointer_tokens(self):
        out = apply_patch({"a/b": 1}, [{"op": "replace", "path": "/a~1b", "value": 2}])
        self.assertEqual(out["a/b"], 2)

    def test_get(self):
        self.assertEqual(get(doc(), "/list/id=y/v"), 2)
        self.assertEqual(get(doc(), ""), doc())


if __name__ == "__main__":
    unittest.main()
