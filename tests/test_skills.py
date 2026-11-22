import tempfile
import unittest

from harness.skills import SkillError, SkillStore


class SkillTests(unittest.TestCase):
    def test_save_list_read_and_delete(self):
        with tempfile.TemporaryDirectory() as root:
            store = SkillStore(root)
            store.save("board-notes", "Board inspection notes", "# Notes\nUse device.info().")
            entries = store.list()
            self.assertEqual(1, len(entries))
            self.assertEqual("board-notes", entries[0]["name"])
            self.assertEqual("Board inspection notes", entries[0]["description"])
            bundle = store.read_bundle("board-notes")
            self.assertIn("Use device.info()", bundle["content"])
            self.assertIn("description: Board inspection notes", bundle["content"])
            store.delete("board-notes")
            self.assertEqual([], store.list())
            with self.assertRaises(SkillError):
                store.load("board-notes")

    def test_skill_requires_description(self):
        with tempfile.TemporaryDirectory() as root:
            store = SkillStore(root)
            with self.assertRaises(SkillError):
                store.save("bad", "", "body")

    def test_skill_content_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            store = SkillStore(root)
            with self.assertRaises(SkillError):
                store.save("big", "Big skill", "x" * 11, limit=10)

    def test_reserved_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = SkillStore(root)
            with self.assertRaises(SkillError):
                store.save("__skill", "bad", "body")

    def test_read_bundle_truncates(self):
        with tempfile.TemporaryDirectory() as root:
            store = SkillStore(root)
            store.save("notes", "Notes", "x" * 100)
            bundle = store.read_bundle("notes", limit=30)
            self.assertTrue(bundle["truncated"])
            self.assertLessEqual(len(bundle["content"]), 30)
