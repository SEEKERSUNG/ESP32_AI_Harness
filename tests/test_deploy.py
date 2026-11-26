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
        certs = root / "certs"
        certs.mkdir()
        self.ca_bundle = certs / "ca-bundle.pem"
        self.ca_bundle.write_text("# test CA bundle\n", encoding="utf-8")
        self.device = device
        self.manifest = root / "build" / "deploy-manifest.json"
        for name, value in (
            ("DEVICE", device),
            ("CA_BUNDLE", self.ca_bundle),
            ("MANIFEST", self.manifest),
        ):
            patcher = mock.patch.object(deploy, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_run_uses_mpremote_from_the_current_python_environment(self):
        completed = mock.Mock(stdout="")
        with mock.patch.object(deploy.subprocess, "run",
                               return_value=completed) as run, \
                mock.patch("builtins.print"):
            result = deploy.run("COM0", "fs", "ls", ":", capture=True)
        command = run.call_args.args[0]
        self.assertEqual(
            [deploy.sys.executable, "-m", "mpremote", "connect", "COM0",
             "fs", "ls", ":"], command)
        self.assertIs(result, completed)

    def test_current_entries_tracks_harness_boot_and_ca_files(self):
        entries = deploy.current_entries()
        self.assertEqual(
            {"harness/__init__.py", "harness/agent.py", "boot.py", "main.py",
             "ca.pem"},
            set(entries))

    def test_changed_deploy_tracks_init_version_file(self):
        entries = deploy.current_entries()
        (self.device / "harness" / "__init__.py").write_text(
            '__version__ = "0.5.0"\n', encoding="utf-8")
        updated = deploy.current_entries()
        self.assertNotEqual(
            entries["harness/__init__.py"], updated["harness/__init__.py"])

    def test_changed_deploy_copies_changed_init_file(self):
        deploy.save_manifest(deploy.current_entries())
        init = self.device / "harness" / "__init__.py"
        init.write_text('__version__ = "0.5.0"\n', encoding="utf-8")
        calls = []

        def fake_run(port, *args, capture=False):
            calls.append((port, args, capture))
            return mock.Mock(stdout="")

        with mock.patch.object(deploy, "run", side_effect=fake_run), \
                mock.patch("builtins.print"):
            deploy.deploy("COM0", reset=False, use_mpy=False, changed=True)
        self.assertIn(
            ("COM0", ("fs", "cp", str(init), ":harness/__init__.py"), False),
            calls)
        self.assertFalse(any(
            args[:2] == ("fs", "cp") and ":harness/agent.py" in args
            for _, args, _ in calls))

    def test_deploy_copies_full_ca_bundle(self):
        calls = []

        def fake_run(port, *args, capture=False):
            calls.append((port, args, capture))
            return mock.Mock(stdout="")

        with mock.patch.object(deploy, "run", side_effect=fake_run), \
                mock.patch.object(deploy, "load_manifest", return_value=None), \
                mock.patch.object(deploy, "save_manifest"), \
                mock.patch("builtins.print"):
            deploy.deploy("COM0", reset=False, use_mpy=False)
        self.assertIn(
            ("COM0", ("fs", "cp", str(self.ca_bundle), ":ca.pem"), False),
            calls)

    def test_manifest_round_trip_and_change_detection(self):
        entries = deploy.current_entries()
        deploy.save_manifest(entries)
        self.assertEqual(entries, deploy.load_manifest())
        (self.device / "harness" / "agent.py").write_text(
            "value = 2\n", encoding="utf-8")
        updated = deploy.current_entries()
        changed = [key for key in updated if entries.get(key) != updated[key]]
        self.assertEqual(["harness/agent.py"], changed)

    def test_ca_bundle_change_is_detected(self):
        entries = deploy.current_entries()
        self.ca_bundle.write_text("# updated CA bundle\n", encoding="utf-8")
        updated = deploy.current_entries()
        self.assertNotEqual(entries["ca.pem"], updated["ca.pem"])

    def test_clean_and_changed_cannot_be_combined(self):
        with self.assertRaises(SystemExit):
            deploy.deploy("COM0", clean=True, changed=True)


if __name__ == "__main__":
    unittest.main()
