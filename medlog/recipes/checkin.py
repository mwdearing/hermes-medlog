"""Daily check-in: list dose slots with no record. Read-only, plain text on stdout.

Prints NOTHING when no medication is registered or nothing is missing, so a scheduler delivers nothing on a quiet
day. Never writes a record and gives no advice. Run it as ``python -m medlog.recipes.checkin`` (or ``medlog-checkin``).
Env: MEDLOG_CHECKIN_DAYS (default 3) and the usual MEDLOG_* settings.
"""
from __future__ import annotations

import os
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta

from ..core import MedlogError, data_dir, missing_for_registry, now

MAX_SLOTS = 8


def fmt_day(text: str) -> str:
    day = date.fromisoformat(text)
    return f"{day:%b} {day.day}"


def held_notice(current: datetime) -> list[str]:
    """One line when the newest automatic import of the last 48 h was held (counts only, from auto-import.log)."""
    try:
        lines = (data_dir() / "auto-import.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    cutoff, newest = current - timedelta(hours=48), None
    for line in lines:
        try:
            when = datetime.fromisoformat(line.split(" ", 1)[0])
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=current.tzinfo)
        if when >= cutoff:
            newest = line
    if newest and "result=held:" in newest:
        reason = newest.split("result=held:", 1)[1].split()[0]
        return [f"A sync was not applied automatically ({reason}). Preview it with import-bridge before applying."]
    return []


def build_message(days: int = 3) -> str:
    current = now()
    rows = missing_for_registry(None, days)
    notice = held_notice(current)
    if not rows and not notice:
        return ""
    lines = [f"Medication check-in ({current:%Y-%m-%d %H:%M})"]
    if rows:
        rows.sort(key=lambda r: (r["date"], r["med"]))
        shown, extra = rows[-MAX_SLOTS:], max(0, len(rows) - MAX_SLOTS)
        grouped: "OrderedDict[str, list[str]]" = OrderedDict()
        for row in shown:
            grouped.setdefault(row["name"], []).append(f"{fmt_day(row['date'])} {row['slot']}")
        lines.append("Not logged:")
        lines += [f"- {name}: {'; '.join(slots)}" for name, slots in grouped.items()]
        if extra:
            lines.append(f"+{extra} older not listed")
        lines.append("Log them in your Health app and sync, or record them with medlog.")
    if notice:
        lines.append("Import held:")
        lines += [f"- {n}" for n in notice]
    return "\n".join(lines)


def main() -> int:
    try:
        message = build_message(int(os.environ.get("MEDLOG_CHECKIN_DAYS", "3")))
    except MedlogError as exc:
        print(f"Medication check-in failed: {exc}")
        return 0
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
