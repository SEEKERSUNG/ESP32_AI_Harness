import pathlib
import tempfile
import unittest
from unittest import mock

from tools import deploy


class DeployManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = pathlib.Path(self.directory.name)
        device = root / "device"
        harness = device / "harness"
        harness.mkdir(parents=True)
        (harness / "__init__.py").write_text("", encoding="utf-8")
        (harness / "agent.py").write_text("value = 1\n", encoding="utf-8")
        (device / "boot.py").write_text("# boot\n", encoding="utf-8")
        (device / "main.py").write_text("# main\n", encoding="utf-8")
        self.device = device
        self.manifest = root / "build" / "deploy-manifest.json"
        for name, value in (("DEVICE", device), ("MANIFEST", self.manifest)):
            patcher = mock.patch.object(deploy, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_current_entries_tracks_harness_and_boot_files(self):
        entries = deploy.current_entries()
        self.assertEqual(
            {"harness/agent.py", "boot.py", "main.py"}, set(entries))

    def test_manifest_round_trip_and_change_detection(self):
        entries = deploy.current_entries()
        deploy.save_manifest(entries)
        self.assertEqual(entries, deploy.load_manifest())
        (self.device / "harness" / "agent.py").write_text(
            "value = 2\n", encoding="utf-8")
        updated = deploy.current_entries()
        changed = [key for key in updated if entries.get(key) != updated[key]]
        self.assertEqual(["harness/agent.py"], changed)

    def test_clean_and_changed_cannot_be_combined(self):
        with self.assertRaises(SystemExit):
            deploy.deploy("COM0", clean=True, changed=True)


if __name__ == "__main__":
    unittest.main()
