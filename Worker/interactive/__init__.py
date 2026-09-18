"""Trusted host-installed interactive execution components."""

# The installed repository supplies the common Access parser. Access's image
# includes only its own package; no Worker/Docker implementation is copied there.
import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parents[2])
if _root not in sys.path:
    sys.path.insert(0, _root)
