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
