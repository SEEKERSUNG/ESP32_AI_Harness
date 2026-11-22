#!/usr/bin/env python3
"""Serial port selection shared by the host-side tools.

Resolution order:

1. an explicit ``--port`` value;
2. the ``HARNESS_PORT`` environment variable;
3. the only USB serial adapter found;
4. the only serial port found.

No machine-specific port is baked in, so the tools work unchanged on
Windows, Linux and macOS.
"""

import os


USB_HINTS = (
    "ch340",
    "ch341",
    "ch343",
    "ch910",
    "cp210",
    "ft232",
    "usb serial",
    "usb jtag",
    "acm",
    "usbmodem",
)


def default_port():
    """Return the best guess for a board serial port, or None."""
    override = os.environ.get("HARNESS_PORT", "").strip()
    if override:
        return override
    try:
        from serial.tools import list_ports
    except ImportError:
        return None

    ports = list(list_ports.comports())
    usb_ports = []
    for info in ports:
        label = "%s %s %s" % (
            info.device,
            info.description or "",
            info.hwid or "",
        )
        label = label.lower()
        if any(hint in label for hint in USB_HINTS):
            usb_ports.append(info.device)
    if len(usb_ports) == 1:
        return usb_ports[0]
    if len(ports) == 1:
        return ports[0].device
    return None


def add_port_argument(parser):
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port (default: HARNESS_PORT or auto-detected USB serial port)",
    )


def resolve_port(parser, value):
    port = value or default_port()
    if not port:
        parser.error(
            "no serial port selected; pass --port COMx (or /dev/ttyACM0) "
            "or set HARNESS_PORT"
        )
    return port
