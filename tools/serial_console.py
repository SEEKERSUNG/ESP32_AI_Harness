#!/usr/bin/env python3
"""Simple interactive serial terminal."""

import argparse
import codecs
import sys
import threading
import time

try:
    import serial
except ImportError:
    serial = None

try:
    from ports import add_port_argument, resolve_port
except ImportError:
    from tools.ports import add_port_argument, resolve_port


def utf8_chunks(data, max_bytes):
    """Split bytes without cutting a UTF-8 sequence in half."""
    start = 0
    size = len(data)
    while start < size:
        end = min(start + max_bytes, size)
        while end > start and end < size and (data[end] & 0xC0) == 0x80:
            end -= 1
        if end == start:
            end = min(start + max_bytes, size)
            while end < size and (data[end] & 0xC0) == 0x80:
                end += 1
        yield data[start:end]
        start = end


def configure_stdio():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class EchoFilter:
    """Drop the device's echo of a line so it is displayed only once."""

    def __init__(self):
        self.lock = threading.Lock()
        self.remaining = b""
        self.terminator = 0
        self.deadline = 0.0

    def arm(self, line, timeout=5.0):
        with self.lock:
            self.remaining = line
            self.terminator = 2
            self.deadline = time.monotonic() + timeout

    def feed(self, data):
        with self.lock:
            if (self.remaining or self.terminator) and time.monotonic() > self.deadline:
                self.remaining = b""
                self.terminator = 0
            index = 0
            while index < len(data):
                if self.remaining:
                    if data[index:index + 1] == self.remaining[:1]:
                        self.remaining = self.remaining[1:]
                        index += 1
                        continue
                    self.remaining = b""
                    self.terminator = 0
                    break
                if self.terminator:
                    if data[index:index + 1] in (b"\r", b"\n"):
                        self.terminator -= 1
                        index += 1
                        continue
                    self.terminator = 0
                    break
                break
            return data[index:]


class SerialTerminal:
    def __init__(self, port, baud, chunk, delay):
        self.port = port
        self.serial = serial.Serial(port, baud, timeout=0.1)
        self.chunk = max(1, chunk)
        self.delay = max(0.0, delay)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.echo_filter = EchoFilter()
        self.reader = threading.Thread(target=self._read_loop, daemon=True)

    def _emit(self, text):
        with self.lock:
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except (OSError, ValueError):
                self.stop.set()

    def _read_loop(self):
        try:
            while not self.stop.is_set():
                data = self.serial.read(self.serial.in_waiting or 1)
                if not data:
                    continue
                data = self.echo_filter.feed(data)
                if not data:
                    continue
                text = self.decoder.decode(data)
                if text:
                    self._emit(text)
        except Exception as exc:
            if not self.stop.is_set():
                self._emit("\n[serial reader stopped: %s]\n" % exc)
                self.stop.set()

    def _write(self, data):
        try:
            for chunk in utf8_chunks(data, self.chunk):
                self.serial.write(chunk)
                self.serial.flush()
                time.sleep(self.delay)
            return True
        except serial.SerialException as exc:
            self._emit("\n[serial write failed: %s]\n" % exc)
            self.stop.set()
            return False

    def run(self):
        self._emit(
            "Serial terminal: %s @ %s\n"
            "Type a line and press Enter. :quit or :exit to leave; "
            "Ctrl-C sends 0x03.\n"
            "If no device prompt appears, press Enter to send a blank line.\n\n"
            % (self.port, self.serial.baudrate)
        )
        self.reader.start()
        try:
            while not self.stop.is_set():
                try:
                    line = input()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    self._write(b"\x03")
                    continue
                if line.strip() in (":quit", ":exit"):
                    break
                encoded = line.encode("utf-8")
                timeout = max(
                    5.0,
                    (len(encoded) + self.chunk) / self.chunk * self.delay + 5.0,
                )
                self.echo_filter.arm(encoded, timeout)
                if not self._write(encoded + b"\n"):
                    break
        finally:
            self.stop.set()
            self.reader.join(timeout=1.0)
            try:
                self.serial.close()
            except Exception:
                pass


def main():
    if serial is None:
        print(
            "pyserial is required: python -m pip install pyserial",
            file=sys.stderr,
        )
        raise SystemExit(1)
    parser = argparse.ArgumentParser(description=__doc__)
    add_port_argument(parser)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--chunk", type=int, default=16,
        help="target bytes per input chunk; UTF-8 sequences are never split "
             "(default: 16)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.02,
        help="seconds between input chunks (default: 0.02)",
    )
    args = parser.parse_args()
    port = resolve_port(parser, args.port)
    configure_stdio()
    try:
        terminal = SerialTerminal(port, args.baud, args.chunk, args.delay)
    except serial.SerialException as exc:
        print("Cannot open %s: %s" % (port, exc), file=sys.stderr)
        raise SystemExit(1)
    try:
        terminal.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
