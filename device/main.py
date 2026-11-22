import os
import sys


def in_maintenance():
    try:
        os.stat("/maintenance.flag")
        return True
    except OSError:
        return False


if in_maintenance():
    print("Maintenance mode: agent is stopped. Deploy files or remove /maintenance.flag and reset.")
else:
    from harness.console import Application
    try:
        Application().run()
    except BaseException as exc:
        # If the watchdog is active it will recover the console after a reset.
        sys.print_exception(exc)
        raise
