"""Every published skill carries the safety framing, and the manifest registers nothing. Runs in the public export too."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md")) if (ROOT / "skills").is_dir() else sorted((ROOT / "public" / "skills").glob("*/SKILL.md"))
MANIFEST = ROOT / "plugin.json" if (ROOT / "plugin.json").exists() else ROOT / "public" / "plugin.json"


def test_there_are_skills():
    assert len(SKILLS) >= 6


def test_every_skill_states_the_safety_lines():
    for path in SKILLS:
        text = path.read_text(encoding="utf-8").lower()
        assert "not medical advice" in text, path
        assert "never infers" in text or "never infer" in text, path
        assert "no dosing" in text, path
        assert "prescriber or pharmacist" in text, path


def test_skill_front_matter_and_distinctive_descriptions():
    seen = set()
    for path in SKILLS:
        front = re.match(r"---\nname: (.+)\ndescription: (.+)\n", path.read_text(encoding="utf-8"))
        assert front, path
        assert front.group(1) == path.parent.name
        head = front.group(2)[:55]
        assert head not in seen, f"description start is not distinctive: {head!r}"
        seen.add(head)


def test_manifest_registers_nothing_and_has_no_capabilities():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["name"] == "medlog"
    assert not manifest.get("capabilities")
    assert manifest["license"] == "Apache-2.0"
