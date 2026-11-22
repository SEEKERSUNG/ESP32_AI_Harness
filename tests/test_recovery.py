import tempfile
import unittest

from harness.recovery import Recovery


class RecoveryTests(unittest.TestCase):
    def test_interrupted_program_is_recovered_once(self):
        with tempfile.TemporaryDirectory() as root:
            recovery = Recovery(root)
            recovery.begin("print('interrupted')")
            self.assertEqual("print('interrupted')", recovery.consume())
            self.assertIsNone(recovery.consume())

    def test_normal_completion_clears_recovery_state(self):
        with tempfile.TemporaryDirectory() as root:
            recovery = Recovery(root)
            recovery.begin("print('ok')")
            recovery.end()
            self.assertIsNone(recovery.consume())


if __name__ == "__main__":
    unittest.main()
