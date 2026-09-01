import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.script import normalize_name, parse  # noqa: E402

SAMPLE = """Title: Kitchen

INT. KITCHEN - MORNING

Maya stands at the counter. The kettle clicks off.

MAYA
(without turning)
You're late.

DAN
The bridge was closed.

He sits down heavily.

EXT. STREET - NIGHT

Rain.
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.script = parse(SAMPLE)

    def test_scenes(self):
        self.assertEqual([s.id for s in self.script.scenes], ["s01", "s02"])
        self.assertEqual(self.script.scenes[0].location, "KITCHEN")
        self.assertEqual(self.script.scenes[0].time_of_day, "MORNING")
        self.assertTrue(self.script.scenes[0].interior)
        self.assertFalse(self.script.scenes[1].interior)

    def test_ids_are_stable_and_typed(self):
        ids = [l.id for l in self.script.scenes[0].lines]
        self.assertEqual(ids, ["s01_b001", "s01_l001", "s01_l002", "s01_b002"])

    def test_dialogue_carries_speaker_and_parenthetical(self):
        line = self.script.scenes[0].lines[1]
        self.assertEqual((line.kind, line.speaker, line.parenthetical, line.text),
                         ("dialogue", "MAYA", "without turning", "You're late."))

    def test_characters_and_locations(self):
        self.assertEqual(self.script.characters(), ["MAYA", "DAN"])
        self.assertEqual(self.script.locations(), ["KITCHEN", "STREET"])

    def test_title(self):
        self.assertEqual(self.script.title, "Kitchen")

    def test_normalize_name(self):
        self.assertEqual(normalize_name("OLD MAN"), "old_man")
        self.assertEqual(normalize_name("Dr. O'Neil"), "dr_o_neil")


if __name__ == "__main__":
    unittest.main()
