from __future__ import annotations

import ctypes
import ctypes.util


path = ctypes.util.find_library("gtk4-layer-shell")
if not path:
    raise ImportError("gtk4-layer-shell is not installed")
library = ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
