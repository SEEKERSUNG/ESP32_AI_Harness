import json
import pathlib
import tempfile
import unittest
from unittest import mock

from harness.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_secret_separation(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            config.set("model", "demo")
            config.set_secret("api_key", "do-not-publish")
            loaded = Config(root)
            self.assertEqual("demo", loaded.get("model"))
            self.assertEqual("do-not-publish", loaded.secret("api_key"))
            self.assertNotIn("api_key", loaded.public_values())
            self.assertNotIn("do-not-publish", (pathlib.Path(root) / "config.json").read_text())
            self.assertIn("do-not-publish", (pathlib.Path(root) / "secrets.json").read_text())

    def test_model_validation_reports_missing_fields(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            with self.assertRaises(ConfigError) as caught:
                config.validate_model()
            self.assertIn("model", str(caught.exception))
            self.assertIn("api_key", str(caught.exception))

    def test_removed_tool_settings_are_dropped(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "config.json"
            path.write_text(json.dumps({
                "auto_save_tools": True,
                "tools_in_prompt": 5,
                "tool_data_files": 8,
            }), encoding="utf-8")
            config = Config(root)
            self.assertNotIn("auto_save_tools", config.values)
            self.assertNotIn("tools_in_prompt", config.values)
            self.assertNotIn("tool_data_files", config.values)

    def test_removed_deepseek_search_settings_are_dropped_and_saved(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "config.json"
            secrets_path = pathlib.Path(root) / "secrets.json"
            path.write_text(json.dumps({
                "search_provider": "deepseek",
                "search_url": "https://api.deepseek.com/v1/tools/search",
                "search_content_limit": 2048,
                "search_include_content": True,
            }), encoding="utf-8")
            secrets_path.write_text(json.dumps({
                "search_api_key": "old-search-secret",
                "api_key": "model-secret",
            }), encoding="utf-8")
            config = Config(root)
            self.assertEqual("auto", config.get("search_provider"))
            for key in ("search_url", "search_content_limit",
                        "search_include_content"):
                self.assertNotIn(key, config.values)
                self.assertNotIn(key, json.loads(path.read_text()))
            self.assertNotIn("search_api_key", config.secrets)
            self.assertNotIn(
                "search_api_key", json.loads(secrets_path.read_text()))
            self.assertEqual("model-secret", config.secret("api_key"))

    def test_thinking_level_persists_and_retired_levels_migrate(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            self.assertEqual("high", config.get("thinking_level"))
            config.set("thinking_level", "max")
            self.assertEqual("max", Config(root).get("thinking_level"))
            path = pathlib.Path(root) / "config.json"
            for retired, expected in (("medium", "high"),
                                      ("middle", "high"),
                                      ("default", "high"),
                                      ("nonsense", "high")):
                path.write_text(json.dumps({"thinking_level": retired}),
                                encoding="utf-8")
                self.assertEqual(expected, Config(root).get("thinking_level"))

    def test_legacy_stream_idle_timeout_is_migrated(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "config.json"
            path.write_text(json.dumps({"stream_idle_timeout": 120}),
                            encoding="utf-8")
            self.assertEqual(300, Config(root).get("stream_idle_timeout"))
            # Once the first-token key exists, an explicit 120 is respected.
            path.write_text(json.dumps({
                "stream_idle_timeout": 120,
                "stream_first_token_timeout": 600,
            }), encoding="utf-8")
            self.assertEqual(120, Config(root).get("stream_idle_timeout"))

    def test_legacy_web_history_window_is_migrated(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "config.json"
            path.write_text(json.dumps({
                "web_history_limit": 50,
                "web_history_chars": 32768,
            }), encoding="utf-8")
            config = Config(root)
            self.assertEqual(200, config.get("web_history_limit"))
            self.assertEqual(131072, config.get("web_history_chars"))
            path.write_text(json.dumps({
                "web_history_limit": 50,
                "web_history_chars": 32768,
                "stream_first_token_timeout": 600,
            }), encoding="utf-8")
            # Still migrated: the marker key is unrelated to these two.
            self.assertEqual(200, Config(root).get("web_history_limit"))

    def test_thinking_text_is_visible_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            self.assertTrue(config.get("show_thinking"))

    def test_reset_memory_restores_defaults_without_writing(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            config.set("model", "demo")
            config.set_secret("api_key", "secret")
            config.reset_memory()
            self.assertEqual("", config.get("model"))
            self.assertEqual("", config.secret("api_key"))
            loaded = Config(root)
            self.assertEqual("demo", loaded.get("model"))
            self.assertEqual("secret", loaded.secret("api_key"))

    def test_missing_ca_file_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            config.set("model", "demo", save=False)
            config.set("ca_file", root + "/missing-ca.pem", save=False)
            config.set_secret("api_key", "test", save=False)
            with self.assertRaises(ConfigError) as caught:
                config.validate_model()
            self.assertIn("CA file is missing", str(caught.exception))

    def test_save_all_restores_both_files_when_second_write_fails(self):
        with tempfile.TemporaryDirectory() as root:
            config = Config(root)
            config.set("model", "old-model")
            config.set_secret("api_key", "old-secret")
            config_path = pathlib.Path(root) / "config.json"
            secrets_path = pathlib.Path(root) / "secrets.json"
            old_config = config_path.read_text()
            old_secrets = secrets_path.read_text()
            config.values["model"] = "new-model"
            config.secrets["api_key"] = "new-secret"

            from harness import config as config_module
            original_write_json = config_module.write_json

            def fail_secrets(path, value):
                if path.endswith("secrets.json"):
                    raise OSError("disk full")
                return original_write_json(path, value)

            with mock.patch("harness.config.write_json", side_effect=fail_secrets):
                with self.assertRaises(OSError):
                    config.save_all()
            self.assertEqual(old_config, config_path.read_text())
            self.assertEqual(old_secrets, secrets_path.read_text())
            loaded = Config(root)
            self.assertEqual("old-model", loaded.get("model"))
            self.assertEqual("old-secret", loaded.secret("api_key"))


if __name__ == "__main__":
    unittest.main()
