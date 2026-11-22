#!/usr/bin/env python3
"""Verify /repl stops the app in maintenance mode; redeploy to resume."""

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
        ser.write(b"/repl\n")
        response = read_until(ser, b"[y/N]")
        print(response.decode(errors="replace"))
        ser.write(b"y\n")
        response = read_until(ser, b"Maintenance mode:", timeout=20)
        print(response.decode(errors="replace"))
    print("MAINTENANCE_SMOKE_OK")


if __name__ == "__main__":
    main()
