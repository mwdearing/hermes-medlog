"""Weekly report: automatic imports of the last 7 days (from auto-import.log) and doses still not logged. Read-only,
plain text on stdout. Run as ``python -m medlog.recipes.weekly_report`` (or ``medlog-weekly-report``)."""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timedelta

from ..core import MedlogError, data_dir, missing_for_registry, now


def import_totals(current: datetime) -> tuple[int, int, int, int]:
    imports = doses = corrections = held = 0
    try:
        lines = (data_dir() / "auto-import.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0, 0, 0
    for line in lines:
        try:
            when = datetime.fromisoformat(line.split(" ", 1)[0])
        except ValueError:
            continue
        when = when if when.tzinfo else when.replace(tzinfo=current.tzinfo)
        if when < current - timedelta(days=7):
            continue
        imports += 1
        doses += int((re.search(r"imported=(\d+)", line) or [0, 0])[1])
        corrections += int((re.search(r"corrected=(\d+)", line) or [0, 0])[1])
        held += "result=held:" in line
    return imports, doses, corrections, held


def build_report() -> str:
    current = now()
    imports, doses, corrections, held = import_totals(current)
    gaps = Counter(row["name"] for row in missing_for_registry(None, 7))
    lines = [f"Weekly medication sync ({current:%Y-%m-%d})", "Imports:",
             f"- Automatic imports: {imports}", f"- Doses imported: {doses}",
             f"- Time corrections from Health: {corrections}", f"- Held: {held}", "Not logged:"]
    lines += [f"- {name} x{count}" for name, count in sorted(gaps.items())] or ["- none"]
    if held:
        lines.append("A held import needs a look: preview it with import-bridge before applying.")
    return "\n".join(lines)


def main() -> int:
    try:
        print(build_report())
    except MedlogError as exc:
        print(f"Weekly medication sync failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
