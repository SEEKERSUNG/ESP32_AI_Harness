#!/usr/bin/env python3
"""Emergency entry to raw REPL on auto-reset ESP32 dev boards."""

import argparse
import time

import serial

try:
    from ports import add_port_argument, resolve_port
except ImportError:
    from tools.ports import add_port_argument, resolve_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_port_argument(parser)
    args = parser.parse_args()
    port = resolve_port(parser, args.port)
    with serial.Serial(port, 115200, timeout=0.05) as ser:
        ser.dtr = False
        ser.rts = True
        time.sleep(0.1)
        ser.rts = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            ser.write(b"\x03")
            data = ser.read(ser.in_waiting or 1)
            if data:
                print(data.decode(errors="replace"), end="")
            time.sleep(0.05)
    print("\nRaw REPL interruption sent. Verify with mpremote before deployment.")


if __name__ == "__main__":
    main()
