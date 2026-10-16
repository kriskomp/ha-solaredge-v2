"""Shared helpers for loading the recorded API responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    """Load a JSON fixture recorded from the live API (sanitized)."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
