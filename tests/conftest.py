import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
for d in ("scripts", "mcp"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)
