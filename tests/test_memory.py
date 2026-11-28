import tempfile
import unittest

from harness.memory import MemoryStore
from tests.support import DummyConfig


class MemoryTests(unittest.TestCase):
    def test_memory_is_created_and_appended(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig())
            self.assertIn("Project Memory", memory.load())
            memory.append("Use 115200 baud.")
            self.assertIn("Use 115200 baud.", memory.load())

    def test_memory_append_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig(memory_append_limit=32))
            with self.assertRaises(Exception):
                memory.append("x" * 40)

    def test_memory_append_default_allows_a_normal_note(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig())
            text = "Anthropic tool_result 必须紧跟 tool_use；" + ("x" * 500)
            result = memory.append(text)
            self.assertEqual(len(text), result["chars"])
            self.assertIn(text, memory.load())

    def test_memory_clear(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig())
            memory.append("note")
            memory.clear()
            self.assertNotIn("note", memory.load())

    def test_memory_delete_removes_matching_lines(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig())
            memory.append("Board serial uses 115200 baud.")
            memory.append("Display uses address 0x3C.")
            result = memory.delete("0x3C")
            self.assertEqual(1, result["removed"])
            text = memory.load()
            self.assertNotIn("0x3C", text)
            self.assertIn("115200", text)
            self.assertTrue(text.endswith("\n"))
            with self.assertRaises(Exception):
                memory.delete("no such line")

    def test_memory_replace_requires_unique_match(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig())
            memory.append("Board serial uses 115200 baud.")
            result = memory.replace("serial", "Board serial uses 9600 baud.")
            self.assertEqual("Board serial uses 115200 baud.", result["before"])
            self.assertEqual("Board serial uses 9600 baud.", result["after"])
            self.assertIn("9600", memory.load())
            memory.append("Second serial note.")
            with self.assertRaises(Exception) as caught:
                memory.replace("serial", "Changed.")
            self.assertIn("more specific", str(caught.exception))
            with self.assertRaises(Exception):
                memory.replace("missing", "Changed.")

    def test_memory_replace_is_bounded_to_one_line(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryStore(root, DummyConfig(memory_append_limit=32))
            memory.append("short note")
            with self.assertRaises(Exception):
                memory.replace("short", "x" * 40)
            with self.assertRaises(Exception):
                memory.replace("short", "two\nlines")
