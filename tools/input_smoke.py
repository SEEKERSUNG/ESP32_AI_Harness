#!/usr/bin/env python3
"""Verify LFCR/CRLF input synchronization at an interactive yes/no prompt."""

import argparse
import time

import serial

from console_smoke import read_until, wait_prompt
from ports import add_port_argument, resolve_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_port_argument(parser)
    args = parser.parse_args()
    port = resolve_port(parser, args.port)
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        time.sleep(2)
        wait_prompt(ser)
        # Deliberately use the LFCR order that exposed stale-terminator bugs.
        ser.write(b"/repl\n\r")
        first = read_until(ser, b"[y/N]")
        time.sleep(0.2)
        stale = ser.read(ser.in_waiting)
        ser.write(b"n\n\r")
        second = read_until(ser, b"esp32> ")
        transcript = (first + stale + second).decode(errors="replace")
        if "Wi-Fi is not configured" in transcript or "Unknown command" in transcript:
            raise AssertionError("A stale line terminator consumed the next input:\n" + transcript)
        if "Maintenance flag saved" in transcript:
            raise AssertionError("The declined maintenance request was accepted:\n" + transcript)
        print(transcript)

        # Verify the SSID prompt accepts UTF-8 Chinese, then cancel before saving.
        chinese_ssid = "测试WiFi"
        ser.write(b"/wifi\n\r")
        first = read_until(ser, b"Wi-Fi SSID ")
        ser.write((chinese_ssid + "\n\r").encode())
        second = read_until(ser, b"Wi-Fi password")
        raw = first + second
        transcript = raw.decode(errors="replace")
        if chinese_ssid.encode() not in raw:
            raise AssertionError("UTF-8 SSID input was not echoed correctly:\n" + transcript)
        ser.write(b"\x03")
        cancelled = read_until(ser, b"esp32> ")
        transcript += cancelled.decode(errors="replace")
        if "Input cancelled" not in transcript:
            raise AssertionError("Ctrl-C did not cancel the password prompt:\n" + transcript)
        if "Wi-Fi settings saved" in transcript:
            raise AssertionError("Cancelled Wi-Fi setup was saved:\n" + transcript)
        print(transcript)
    print("INPUT_SMOKE_OK")


if __name__ == "__main__":
    main()
