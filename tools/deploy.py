#!/usr/bin/env python3
"""Deploy device files with mpremote. Never invokes firmware flashing tools.

The full Mozilla CA bundle is copied to ``/ca.pem`` as part of a normal or
first incremental deployment. ``--changed`` refreshes it only when the local
bundle changed.
"""

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

try:
    from ports import add_port_argument, resolve_port
except ImportError:
    from tools.ports import add_port_argument, resolve_port


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEVICE = ROOT / "device"
CA_BUNDLE = ROOT / "certs" / "ca-bundle.pem"
BUILD = ROOT / "build" / "mpy" / "harness"
MANIFEST = ROOT / "build" / "deploy-manifest.json"

PREPARE_CODE = (
    "import os\n"
    "try:\n"
    "    os.mkdir('/harness')\n"
    "except OSError:\n"
    "    pass\n"
    "try:\n"
    "    for name in os.listdir('/harness/__pycache__'):\n"
    "        os.remove('/harness/__pycache__/' + name)\n"
    "    os.rmdir('/harness/__pycache__')\n"
    "except OSError:\n"
    "    pass\n"
    "for name in %s:\n"
    "    for ext in ('.py', '.mpy'):\n"
    "        try:\n"
    "            os.remove('/harness/' + name + ext)\n"
    "        except OSError:\n"
    "            pass\n"
)

CLEAN_CODE = (
    "import os\n"
    "def _clean(path):\n"
    "    for name in os.listdir(path):\n"
    "        p = path.rstrip('/') + '/' + name if path != '/' else '/' + name\n"
    "        try:\n"
    "            os.remove(p)\n"
    "        except OSError:\n"
    "            _clean(p)\n"
    "            os.rmdir(p)\n"
    "_clean('/')\n"
)


def run(port, *args, capture=False):
    command = ["mpremote", "connect", port, *args]
    print("+", " ".join(command))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )


def find_mpy_cross():
    candidates = [
        ROOT / ".venv" / "Scripts" / "mpy-cross.exe",
        ROOT / ".venv" / "bin" / "mpy-cross",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which("mpy-cross")
    if found:
        return pathlib.Path(found)
    raise FileNotFoundError(
        "mpy-cross not found. Install the matching version, for example:\n"
        "  python -m venv .venv\n"
        "  .venv\\Scripts\\python -m pip install mpy-cross==1.29.0.post2"
    )


def build_mpy(sources):
    compiler = find_mpy_cross()
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    for source in sources:
        target = BUILD / (source.stem + ".mpy")
        subprocess.run([str(compiler), "-o", str(target), str(source)], check=True)
        print(f"compiled {source.name} -> {target.relative_to(ROOT)}")
    return BUILD


def source_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_entries():
    entries = {}
    for source in sorted((DEVICE / "harness").glob("*.py")):
        if source.name == "__init__.py":
            continue
        entries["harness/" + source.name] = source_hash(source)
    for name in ("boot.py", "main.py"):
        entries[name] = source_hash(DEVICE / name)
    if not CA_BUNDLE.exists():
        raise FileNotFoundError(
            "CA bundle not found: %s. Restore certs/ca-bundle.pem before "
            "deploying." % CA_BUNDLE)
    entries["ca.pem"] = source_hash(CA_BUNDLE)
    return entries


def load_manifest():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_manifest(entries):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def deploy(port, clean=False, reset=True, use_mpy=True, changed=False):
    if clean and changed:
        raise SystemExit("--clean and --changed cannot be combined")
    previous = load_manifest()
    entries = current_entries()
    if changed and previous is not None:
        selected = sorted(
            key for key in entries if previous.get(key) != entries[key])
        removed = sorted(key for key in previous if key not in entries)
    else:
        selected = sorted(entries)
        removed = sorted(key for key in previous if key not in entries) \
            if previous else []
    if changed and previous is not None and not selected and not removed:
        print("No source changes since the last deployment.")
        return
    partial = changed and previous is not None
    if partial:
        print("Changed files: " + ", ".join(selected))

    listing = run(port, "fs", "ls", ":", capture=True)
    print("Existing device files:\n" + listing.stdout)
    if clean:
        print("Cleaning only the MicroPython VFS; firmware is not modified.")
        run(port, "exec", CLEAN_CODE)

    sources = sorted((DEVICE / "harness").glob("*.py"))
    selected_sources = [
        source for source in sources
        if not partial or "harness/" + source.name in selected
    ]
    modules = [source.stem for source in selected_sources
               if source.name != "__init__.py"]
    removed_modules = [
        key[len("harness/"):].rsplit(".", 1)[0]
        for key in removed if key.startswith("harness/")
    ]
    # Remove replaced modules (and modules deleted from the source tree) so no
    # stale .py/.mpy survives next to the new file.
    modules = tuple(modules + removed_modules + ([] if partial else ["tools"]))
    if modules:
        run(port, "exec", PREPARE_CODE % (repr(modules),))
    if not partial or "harness/__init__.py" in selected:
        run(port, "fs", "cp", str(DEVICE / "harness" / "__init__.py"),
            ":harness/__init__.py")

    if use_mpy:
        mpy_dir = build_mpy(selected_sources)
        for source in selected_sources:
            if source.name == "__init__.py":
                continue
            artifact = mpy_dir / (source.stem + ".mpy")
            run(port, "fs", "cp", str(artifact), ":harness/" + artifact.name)
    else:
        for source in selected_sources:
            if source.name == "__init__.py":
                continue
            run(port, "fs", "cp", str(source), ":harness/" + source.name)

    if not partial or "boot.py" in selected:
        run(port, "fs", "cp", str(DEVICE / "boot.py"), ":boot.py")
    if not partial or "main.py" in selected:
        run(port, "fs", "cp", str(DEVICE / "main.py"), ":main.py")
    if not partial or "ca.pem" in selected:
        run(port, "fs", "cp", str(CA_BUNDLE), ":ca.pem")
    run(port, "exec", "import os\ntry:\n    os.remove('/maintenance.flag')\nexcept OSError:\n    pass")
    if not partial:
        run(port, "fs", "ls", ":")
    save_manifest(entries)
    if reset:
        run(port, "reset")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_port_argument(parser)
    parser.add_argument("--clean", action="store_true", help="Delete existing VFS files before deployment")
    parser.add_argument("--no-reset", action="store_true", help="Do not reset after copying files")
    parser.add_argument(
        "--changed",
        action="store_true",
        help="Deploy only files whose content changed since the last deploy",
    )
    parser.add_argument(
        "--source",
        action="store_true",
        help="Deploy .py sources instead of precompiled .mpy (may break TLS on classic ESP32)",
    )
    args = parser.parse_args()
    port = resolve_port(parser, args.port)
    try:
        deploy(port, args.clean, not args.no_reset,
               use_mpy=not args.source, changed=args.changed)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print("If the Harness console is running, use /repl first to enter maintenance mode.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
