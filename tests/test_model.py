"""python3 -m unittest discover tests      The cut-list rules and the on-disk store."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from clipkit import model  # noqa: E402

VECTORS = json.loads((ROOT / "tests" / "vectors.json").read_text(encoding="utf-8"))


class Rules(unittest.TestCase):
    def test_vectors(self):
        """The same cases run through web/model.js in tests/model.test.mjs."""
        for v in VECTORS:
            with self.subTest(v["name"]):
                cl = model.normalise(copy.deepcopy(v["start"]))
                for act, body in v["steps"]:
                    cl = model.apply(cl, act, body)
                self.assertEqual([[c["n"], c["in"], c["out"], c["note"]] for c in cl["clips"]], v["expect"])

    def test_unknown_action(self):
        self.assertIsNone(model.apply(model.fresh("t.mp4", 10, 24), "explode", {}))

    def test_not_a_number(self):
        with self.assertRaises(ValueError):
            model.apply(model.fresh("t.mp4", 10, 24), "clip", {"in": "abc", "out": 3})

    def test_note_length(self):
        cl = model.apply(model.fresh("t.mp4", 10, 24), "clip", {"in": 1, "out": 2, "note": "x" * 500})
        self.assertEqual(len(cl["clips"][0]["note"]), 200)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = model.Store(Path(self.tmp.name) / "state", "tape one.mp4", lambda: (100.0, 24.0))

    def tearDown(self):
        self.tmp.cleanup()

    def mark(self, a, b):
        return self.store.save(model.apply(self.store.load(), "clip", {"in": a, "out": b}))

    def test_fresh_when_nothing_saved(self):
        cl = self.store.load()
        self.assertEqual((cl["tape"], cl["duration"], cl["fps"], cl["clips"]), ("tape one.mp4", 100.0, 24.0, []))

    def test_save_and_reload(self):
        self.mark(1, 2)
        self.mark(5, 9)
        self.assertEqual([(c["n"], c["in"], c["out"]) for c in self.store.load()["clips"]], [(1, 1.0, 2.0), (2, 5.0, 9.0)])

    def test_undo_and_redo(self):
        self.mark(1, 2)
        self.mark(5, 9)
        self.assertEqual(len(self.store.undo("back")["clips"]), 1)
        self.assertEqual(len(self.store.undo("fwd")["clips"]), 2)
        self.assertEqual(len(self.store.undo("fwd")["clips"]), 2)        # nothing left to redo

    def test_a_new_edit_clears_redo(self):
        self.mark(1, 2)
        self.mark(5, 9)
        self.store.undo("back")
        self.mark(20, 30)
        self.assertEqual([c["in"] for c in self.store.undo("fwd")["clips"]], [1.0, 20.0])

    def test_broken_file_falls_back_to_fresh(self):
        self.mark(1, 2)
        self.store.file.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load()["clips"], [])

    def test_snapshot_keeps_a_dated_copy(self):
        self.mark(1, 2)
        self.store.snapshot()
        self.store.snapshot()
        self.assertEqual(len(list(self.store.backups.glob("cutlist_*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
