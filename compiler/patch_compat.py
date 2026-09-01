"""Re-export of server.patch so both packages can import JSON Patch without a
server -> compiler -> server cycle. No bpy."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.patch import PatchError, apply_patch, get  # noqa: E402,F401
