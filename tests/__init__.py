import pathlib
import sys


DEVICE = pathlib.Path(__file__).resolve().parents[1] / "device"
if str(DEVICE) not in sys.path:
    sys.path.insert(0, str(DEVICE))
