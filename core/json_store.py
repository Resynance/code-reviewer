"""Small helpers for JSON-backed local stores."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


def read_json_file(path: Path, default: Any, *, expected_type: type | tuple[type, ...] | None = None) -> Any:
    """Read a JSON store, returning ``default`` only when the file is absent."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{path} contains invalid JSON; refusing to use default data") from exc

    if expected_type is not None and not isinstance(data, expected_type):
        expected = (
            ", ".join(t.__name__ for t in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise RuntimeError(f"{path} must contain {expected} JSON data")
    return data
