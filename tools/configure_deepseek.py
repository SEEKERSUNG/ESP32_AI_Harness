"""Run with: mpremote connect <PORT> run tools/configure_deepseek.py"""

from harness.config import Config


def main():
    config = Config()
    config.set("base_url", "https://api.deepseek.com", save=False)
    config.set("model", "deepseek-flash", save=False)
    config.set("ca_file", "/ca.pem", save=False)
    config.set("tls_verify", True, save=False)
    config.save()
    print("DeepSeek non-secret settings saved; Wi-Fi and API key are unchanged.")


main()
