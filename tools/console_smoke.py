#!/usr/bin/env python3
"""Smoke-test the running serial console without using cloud credentials."""

import argparse
import sys
import time

import serial

try:
    from ports import add_port_argument, resolve_port
except ImportError:
    from tools.ports import add_port_argument, resolve_port

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


def read_until(ser, marker, timeout=15):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            data.extend(chunk)
            if marker in data:
                return bytes(data)
        if len(data) > 65536:
            del data[:-32768]
    raise TimeoutError(f"Did not receive {marker!r}; got:\n{data.decode(errors='replace')}")


def wait_prompt(ser, marker=b"esp32> ", timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        ser.write(b"\n")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Console prompt did not become ready")
        try:
            output = read_until(ser, marker, timeout=min(2, remaining))
        except TimeoutError:
            continue
        time.sleep(0.2)
        ser.reset_input_buffer()
        return output


def run(port):
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        time.sleep(2)
        transcript = bytearray(wait_prompt(ser))
        for command, expected in (
            (b"/status\n", b"Firmware: MicroPython"),
            (b"/help\n", b"Commands:"),
            (b"/context\n", b"Context:"),
        ):
            ser.write(command)
            response = read_until(ser, b"esp32> ")
            transcript.extend(response)
            if expected not in response:
                raise AssertionError(f"Missing {expected!r} in {response!r}")
        print(transcript.decode(errors="replace"))
        print("CONSOLE_SMOKE_OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_port_argument(parser)
    args = parser.parse_args()
    run(resolve_port(parser, args.port))


if __name__ == "__main__":
    main()
