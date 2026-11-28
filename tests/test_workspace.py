import os
import tempfile
import unittest

from harness.workspace import Workspace, WorkspaceError
from tests.support import DummyConfig


class WorkspaceTests(unittest.TestCase):
    def test_write_read_list_and_stat(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            result = workspace.write("notes/hello.txt", "hello")
            self.assertEqual("/workspace/notes/hello.txt", result["path"])
            self.assertEqual("hello", workspace.read("notes/hello.txt")["text"])
            entries = workspace.list("notes")
            self.assertEqual(1, len(entries))
            self.assertEqual("file", entries[0]["kind"])
            self.assertEqual("file", workspace.stat("notes/hello.txt")["kind"])
            root_entries = workspace.list("")
            dirs = [entry for entry in root_entries if entry["kind"] == "dir"]
            self.assertTrue(all(entry["size"] == 0 for entry in dirs))

    def test_append_and_replace_edit_only_the_changed_fragment(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("tmp/script.py", "a = 1\nb = 2\n")
            appended = workspace.append("tmp/script.py", "c = 3\n")
            self.assertEqual(len("a = 1\nb = 2\nc = 3\n"), appended["chars"])
            replaced = workspace.replace(
                "tmp/script.py", "b = 2", "b = 20")
            self.assertEqual(1, replaced["occurrences"])
            self.assertEqual(len("a = 1\nb = 20\nc = 3\n"), replaced["chars"])
            self.assertEqual(
                "a = 1\nb = 20\nc = 3\n",
                workspace.read("tmp/script.py")["text"])
            workspace.replace("tmp/script.py", "c = 3\n", "")
            self.assertEqual(
                "a = 1\nb = 20\n", workspace.read("tmp/script.py")["text"])

    def test_replace_requires_a_unique_match_unless_all(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("a.txt", "x\nx\n")
            with self.assertRaises(WorkspaceError):
                workspace.replace("a.txt", "x", "y")
            result = workspace.replace("a.txt", "x", "y", all=True)
            self.assertEqual(2, result["occurrences"])
            self.assertEqual("y\ny\n", workspace.read("a.txt")["text"])
            with self.assertRaises(WorkspaceError):
                workspace.replace("a.txt", "missing", "z")
            with self.assertRaises(WorkspaceError):
                workspace.replace("a.txt", "", "z")

    def test_replace_failure_suggests_nearby_lines(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write(
                "tmp/script.py",
                "def read_sensor():\n    return 1\n\nprint(read_sensor())\n",
            )
            with self.assertRaises(WorkspaceError) as caught:
                workspace.replace(
                    "tmp/script.py",
                    "def read_sensor():\n\treturn 2",
                    "def read_sensor():\n    return 2",
                )
            message = str(caught.exception)
            self.assertIn("match not found", message)
            self.assertIn("closest lines", message)
            self.assertIn("read_sensor", message)
            self.assertIn("1:", message)

    def test_append_creates_a_missing_file(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            result = workspace.append("tmp/new.py", "print(1)\n")
            self.assertEqual(len("print(1)\n"), result["chars"])
            self.assertEqual(
                "print(1)\n", workspace.read("tmp/new.py")["text"])

    def test_write_bytes_and_limit(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig(
                workspace_file_limit=4, workspace_total_limit=8,
            )
            workspace = Workspace(root, config, clean_tmp=False)
            result = workspace.write_bytes(
                "downloads/a.bin", b"\x00\x01\x02", limit=4
            )
            self.assertEqual("/workspace/downloads/a.bin", result["path"])
            self.assertEqual(3, result["bytes"])
            with open(os.path.join(root, "workspace", "downloads", "a.bin"), "rb") as handle:
                self.assertEqual(b"\x00\x01\x02", handle.read())
            with self.assertRaises(WorkspaceError):
                workspace.write_bytes("downloads/b.bin", b"12345", limit=4)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            for path in ("../escape", "/absolute", "a/../../b", "a\\b"):
                with self.assertRaises(WorkspaceError):
                    workspace.write(path, "x")

    def test_absolute_workspace_paths_are_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("/workspace/notes/a.txt", "ok")
            self.assertEqual("ok", workspace.read("notes/a.txt")["text"])
            self.assertEqual("ok", workspace.read("/workspace/notes/a.txt")["text"])

    def test_tmp_is_cleaned_on_init_but_workspace_survives(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig()
            workspace = Workspace(root, config, clean_tmp=False)
            workspace.write("keep.txt", "keep")
            workspace.write("tmp/scratch.txt", "scratch")
            reloaded = Workspace(root, config, clean_tmp=True)
            self.assertEqual("keep", reloaded.read("keep.txt")["text"])
            self.assertEqual([], reloaded.list("tmp"))

    def test_tmp_survives_reload_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig()
            workspace = Workspace(root, config, clean_tmp=False)
            workspace.write("tmp/reused.py", "print('again')\n")
            reloaded = Workspace(root, config)
            self.assertEqual(
                "print('again')\n", reloaded.read("tmp/reused.py")["text"])

    def test_file_and_total_limits(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig(
                workspace_file_limit=10,
                workspace_total_limit=15,
                workspace_files_limit=2,
            )
            workspace = Workspace(root, config, clean_tmp=False)
            workspace.write("a.txt", "12345")
            with self.assertRaises(WorkspaceError):
                workspace.write("b.txt", "12345678901")
            workspace.write("b.txt", "12345")
            with self.assertRaises(WorkspaceError):
                workspace.write("c.txt", "1")

    def test_remove_and_clean_tmp(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("a.txt", "a")
            workspace.mkdir("dir")
            workspace.write("dir/b.txt", "b")
            with self.assertRaises(WorkspaceError):
                workspace.remove("dir")
            workspace.remove("dir/b.txt")
            workspace.remove("dir")
            workspace.write("tmp/x.txt", "x")
            workspace.clean_tmp()
            self.assertEqual([], workspace.list("tmp"))
