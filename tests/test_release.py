"""Every manifest version must have release notes in CHANGELOG.md."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section_module():
    spec = importlib.util.spec_from_file_location(
        "changelog_section", ROOT / "scripts" / "changelog_section.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_changelog_has_the_manifest_version() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "solaredge_v2" / "manifest.json").read_text()
    )
    notes = _section_module().section(
        manifest["version"], (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    )
    assert "Pierwsze wydanie" in notes or len(notes) > 20


def test_section_stops_at_the_next_version() -> None:
    text = "# C\n\n## 2.0\n\nnew\n\n## 1.0\n\nold\n"
    assert _section_module().section("2.0", text) == "new"
