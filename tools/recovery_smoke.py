#!/usr/bin/env python3
"""Run from maintenance REPL; verify watchdog recovery of a controlled loop."""

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
        wait_prompt(ser, b">>> ")
        command = (
            "import os; os.remove('/maintenance.flag'); "
            "from harness.console import Application; "
            "app=Application(); "
            "app.agent.pending_source='while True: pass'; "
            "app.run()\n"
        )
        ser.write(command.encode())
        print(read_until(ser, b"esp32> ").decode(errors="replace"))
        ser.write(b"/run\n")
        print(read_until(ser, b"[y/N]").decode(errors="replace"))
        ser.write(b"y\n")
        response = read_until(ser, b"esp32> ", timeout=50)
        print(response.decode(errors="replace"))
        if b"previous generated code caused a reset" not in response:
            raise AssertionError("Recovery warning was not shown after watchdog reset")
        ser.write(b"/discard\n")
        print(read_until(ser, b"esp32> ").decode(errors="replace"))
        ser.write(b"/status\n")
        response = read_until(ser, b"esp32> ")
        print(response.decode(errors="replace"))
        if b"Pending code: False" not in response:
            raise AssertionError("Recovered code was not discarded")
    print("RECOVERY_SMOKE_OK")


if __name__ == "__main__":
    main()
