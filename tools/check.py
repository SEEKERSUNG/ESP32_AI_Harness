#!/usr/bin/env python3
"""Fast host gate: byte-compile sources and run unit tests.

The full suite runs in well under a minute, so it is the default. Use
``--quick`` while iterating on agent/config/HTTP/persistence/web code, and the
matching device loop (see docs/development.md) when a change touches real
MicroPython behavior.
"""

import argparse
import compileall
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

QUICK_TESTS = (
    "tests.test_agent",
    "tests.test_background",
    "tests.test_conversation",
    "tests.test_config",
    "tests.test_console_ui",
    "tests.test_executor",
    "tests.test_http_client",
    "tests.test_http_tool",
    "tests.test_llm",
    "tests.test_llm_anthropic",
    "tests.test_web_app",
    "tests.test_web_ui",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick", action="store_true",
        help="run only tests for the agent, config, HTTP, LLM and web layers",
    )
    parser.add_argument(
        "--no-compile", action="store_true", help="skip compileall",
    )
    parser.add_argument(
        "--tests", nargs="+", default=None,
        help="explicit unittest targets (for example tests.test_agent)",
    )
    parser.add_argument(
        "-k", dest="pattern", default="", help="unittest -k name pattern",
    )
    args = parser.parse_args()

    started = time.time()
    if not args.no_compile:
        for folder in ("device", "tools", "tests"):
            if not compileall.compile_dir(
                    str(ROOT / folder), quiet=1, force=True):
                print("compileall failed for %s" % folder, file=sys.stderr)
                return 1
        print("compileall: ok (%.2fs)" % (time.time() - started))

    command = [sys.executable, "-m", "unittest"]
    if args.tests:
        command.extend(args.tests)
    elif args.quick:
        command.extend(QUICK_TESTS)
    else:
        command.extend(["discover", "-s", "tests", "-t", "."])
    if args.pattern:
        command.extend(["-k", args.pattern])
    result = subprocess.run(command, cwd=ROOT)
    print("host checks: %s (%.2fs)" % (
        "ok" if result.returncode == 0 else "FAILED", time.time() - started))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
