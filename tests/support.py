from harness.config import DEFAULTS, SECRET_DEFAULTS


class DummyConfig:
    def __init__(self, **overrides):
        self.values = dict(DEFAULTS)
        self.values.update(overrides)
        self.secrets = dict(SECRET_DEFAULTS)
        self.secrets.update({
            "wifi_ssid": "test-network",
            "wifi_password": "secret",
            "api_key": "test-api-key",
        })
        if "model" not in overrides:
            self.values["model"] = "test-model"

    def get(self, name, default=None):
        return self.values.get(name, default)

    def secret(self, name, default=""):
        return self.secrets.get(name, default)

    def validate_model(self):
        return None


class DummyBoard:
    def capabilities(self):
        return "test board"

    def info(self):
        return {"test": True}
