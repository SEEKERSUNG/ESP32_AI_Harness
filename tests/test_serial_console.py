import unittest

from tools.serial_console import EchoFilter, utf8_chunks


class SerialConsoleTests(unittest.TestCase):
    def test_utf8_chunks_never_split_characters(self):
        data = "中文abc😀".encode("utf-8")
        for size in (1, 2, 3, 4, 5, 16):
            chunks = list(utf8_chunks(data, size))
            self.assertEqual(data, b"".join(chunks))
            for chunk in chunks:
                chunk.decode("utf-8")
        self.assertEqual([b"\xe4\xb8\xad"], list(utf8_chunks("中".encode(), 1)))

    def test_echo_filter_suppresses_only_matching_device_echo(self):
        echo = EchoFilter()
        echo.arm(b"/status")
        self.assertEqual(b"Firmware", echo.feed(b"/status\r\nFirmware"))

        echo.arm("你好".encode("utf-8"))
        self.assertEqual(b"", echo.feed(b"\xe4\xbd\xa0"))
        self.assertEqual(b"", echo.feed(b"\xe5\xa5\xbd"))
        self.assertEqual(b"OK", echo.feed(b"\r\nOK"))

        echo.arm(b"/status")
        self.assertEqual(b"Firmware", echo.feed(b"Firmware"))

        echo.arm(b"")
        self.assertEqual(b"esp32> ", echo.feed(b"\r\nesp32> "))


if __name__ == "__main__":
    unittest.main()
