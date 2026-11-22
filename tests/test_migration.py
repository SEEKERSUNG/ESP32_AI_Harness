import os
import tempfile
import unittest

from harness.config import join_path, write_json, write_text
from harness.migration import migrate_legacy_skills
from harness.skills import SkillStore


class MigrationTests(unittest.TestCase):
    def test_legacy_programs_become_markdown_skills(self):
        with tempfile.TemporaryDirectory() as root:
            old = join_path(root, "skills")
            os.makedirs(old)
            write_text(join_path(old, "legacy_tool.py"),
                       '# TOOL: {"description":"Legacy tool"}\n'
                       "def run(args, api):\n"
                       "    return 'ok'\n")
            write_json(join_path(old, "legacy_tool.json"),
                       {"task": "legacy", "tool": {"description": "Legacy tool"}})
            write_text(join_path(old, "plain.py"), "print(1)")
            write_json(join_path(old, "plain.json"), {"task": "plain task"})

            skills = SkillStore(root)
            count = migrate_legacy_skills(root, skills)
            self.assertEqual(2, count)
            entries = sorted(entry["name"] for entry in skills.list())
            self.assertEqual(["legacy_tool", "plain"], entries)
            self.assertIn("def run", skills.read_bundle("legacy_tool")["content"])
            self.assertIn("print(1)", skills.read_bundle("plain")["content"])
            self.assertFalse(os.path.exists(join_path(old, "plain.py")))


if __name__ == "__main__":
    unittest.main()
