import argparse
import contextlib
import io
import os
import unittest
from unittest import mock

from tools import ports


class PortSelectionTests(unittest.TestCase):
    def test_explicit_port_wins(self):
        parser = argparse.ArgumentParser()
        ports.add_port_argument(parser)
        self.assertEqual("COM9", ports.resolve_port(parser, "COM9"))

    def test_environment_override_is_used(self):
        with mock.patch.dict(os.environ, {"HARNESS_PORT": "/dev/ttyACM0"}):
            self.assertEqual("/dev/ttyACM0", ports.default_port())

    def test_missing_port_is_a_parser_error(self):
        parser = argparse.ArgumentParser()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("tools.ports.default_port", return_value=None):
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit):
                        ports.resolve_port(parser, None)
        self.assertIn("no serial port selected", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
