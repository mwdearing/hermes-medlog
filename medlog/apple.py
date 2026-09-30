"""import_apple - reconcile an Apple Health medication-export JSON into medlog.

Data plumbing only. Names map to registry ids through a JSON map, statuses are
classified, duplicates and mismatches are detected, and matching events are
imported through medlog's own log writer with source ``apple_health``. It never
changes dosing logic, schedules, thresholds or guardrails, and never invents a
dose, time or date.

Input file: a JSON list; each item has
  external_id (string, required), medication (display name),
  status ("taken"/"skipped"/anything else), start (ISO 8601 with UTC offset),
  dose (number or null), unit (string or null).

Map file: ``MEDLOG_APPLE_MAP`` or ``<MEDLOG_HOME>/apple_med_map.json``;
case-insensitive {display name: registry id}.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .core import MedlogError, _setting, data_dir, load_registry, now, pick_slot, slots_for, store_for, tz, void_event

# A daily dose logged before this local hour belongs to the previous day's last slot (a dose at 00:17 is the
# previous evening's, which is how medlog already stores after-midnight doses).
NIGHT_ROLLOVER_HOUR = 4


def map_path() -> Path:
    raw = _setting(lambda: config.get("apple_map", "MEDLOG_APPLE_MAP"))
    if raw:
        return Path(raw).expanduser()
    return data_dir() / "apple_med_map.json"


def load_map() -> dict[str, str]:
    """Case-insensitive {display name: registry id} map."""
    path = map_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, reg_id in raw.items():
        if name and isinstance(reg_id, str) and reg_id.strip():
            out[name.strip().lower()] = reg_id.strip()
    return out


def name_to_id(name: str, the_map: dict[str, str]) -> str | None:
    if not name:
        return None
    return the_map.get(name.strip().lower())


def classify_status(status: str) -> str:
    """"taken"/"skipped" kept; anything else -> "ignored"."""
    return status if status in ("taken", "skipped") else "ignored"


def local_dt(start: str) -> datetime:
    """Parse an ISO 8601 datetime WITH A REQUIRED UTC offset, in local time.

    A naive timestamp (no offset) is rejected rather than silently assigned the local timezone (2026-09-22
    review): the importer contract is that every Apple Health timestamp carries an explicit offset, so a
    naive one means the source data does not meet that contract and must not be guessed at.
    """
    try:
        when = datetime.fromisoformat(start)
    except ValueError as exc:
        raise MedlogError(f"bad start {start!r}, use ISO 8601 with UTC offset") from exc
    if when.tzinfo is None:
        raise MedlogError(f"start {start!r} has no UTC offset, use ISO 8601 with an explicit offset")
    return when.astimezone(tz())


def _is_count(event: dict, med: dict | None = None) -> bool:
    """Apple's Medications app logs a dose as 1 "count": one dose, amount not stated. Not an amount.

    Also true when the medication has a configured dose and Apple's unit is a different one (an injection logged
    in mL against a dose recorded in mg): the two cannot be compared, so the configured dose stands."""
    unit = str(event.get("unit") or "").strip().lower()
    if unit == "count":
        return True
    if med and med.get("dose") is not None and unit and med.get("unit") and unit != str(med["unit"]).strip().lower():
        return True
    return False


def _dose_diff(existing: dict, event: dict, med: dict | None = None) -> float:
    if _is_count(event, med):
        return 0.0
    try:
        return abs(float(existing.get("dose")) - float(event.get("dose")))
    except (TypeError, ValueError):
        return 0.0


def attribute(med: dict, new_dt: datetime) -> tuple[str, str | None]:
    """(date, slot) a dose belongs to. Daily medications roll a before-04:00 dose back to the previous day's
    last slot; weekly/prn medications keep the dose's own local date."""
    day = new_dt.date().isoformat()
    slots = slots_for(med)
    if med.get("kind") not in ("prn", "weekly") and slots and new_dt.hour < NIGHT_ROLLOVER_HOUR:
        return (new_dt.date() - timedelta(days=1)).isoformat(), slots[-1][0]
    return day, pick_slot(med, [], "", day, new_dt.strftime("%H:%M"))


def _is_apple(e: dict) -> bool:
    return e.get("source") == "apple_health" or bool(e.get("apple_id")) or str(e.get("note") or "").startswith("apple:")


def _existing_dt(e: dict, med: dict) -> datetime | None:
    """The moment an existing record describes, undoing the after-midnight date rollover for daily medications."""
    if not e.get("time"):
        return None
    try:
        when = datetime.fromisoformat(f'{e["date"]}T{e["time"]}').replace(tzinfo=tz())
    except ValueError:
        return None
    if med.get("kind") not in ("prn", "weekly") and slots_for(med) and when.hour < NIGHT_ROLLOVER_HOUR:
        when += timedelta(days=1)
    return when


def _clash(e: dict, med: dict, med_id: str, day: str, slot: str | None, new_dt: datetime) -> bool:
    """Does active record `e` already hold this dose's place (weekly: same date; else same date+slot, or within 3 h)?"""
    if e.get("med") != med_id or e.get("voided"):
        return False
    if med.get("kind") == "weekly":
        return e.get("date") == day
    if e.get("date") == day and slot is not None and e.get("slot") == slot and e.get("status") != "extra":
        return True
    existing = _existing_dt(e, med)
    return existing is not None and abs((new_dt - existing).total_seconds()) <= 3 * 3600


def correction_target(event: dict, existing: list[dict], new_dt: datetime, med_id: str) -> dict | None:
    """The active, non-Apple record whose time Apple's time should replace ("Health time wins").

    Only a clean match is corrected: same status, no dose mismatch, a different or missing time. Anything else
    stays a duplicate/mismatch report for a human. Returns None when there is nothing to correct."""
    med = load_registry()["medications"].get(med_id, {})
    day, slot = attribute(med, new_dt)
    apple_time = new_dt.strftime("%H:%M")
    for e in existing:
        if _is_apple(e) or not _clash(e, med, med_id, day, slot, new_dt):
            continue
        if e.get("status") != event["status"] or _dose_diff(e, event, med) > 0.01:
            return None
        return e if e.get("time") != apple_time else None
    return None


def duplicate_check(event: dict, existing: list[dict], new_dt: datetime,
                    med_id: str) -> tuple[bool, bool]:
    """Return (is_duplicate, is_mismatch) for a candidate event.

    A duplicate is the same external_id already imported or repeated in the
    file, or an active event for the same medication on the same local date
    within 3 hours (a weekly medication: the same local date). A mismatch is a
    duplicate whose dose differs from the existing one by more than 0.01.

    A slot-level clash (an Apple dose whose slot on that date already holds an
    active record, timed or not) is caught separately by build_event so an
    untimed record still blocks a duplicate in the same slot.
    """
    ext = event["external_id"]
    med = load_registry()["medications"].get(med_id, {})
    for e in existing:
        if e.get("note") == f"apple:{ext}" or e.get("apple_id") == ext:
            return True, _dose_diff(e, event, med) > 0.01
    day, slot = attribute(med, new_dt)
    for e in existing:
        if _clash(e, med, med_id, day, slot, new_dt):
            return True, _dose_diff(e, event, med) > 0.01
    return False, False


def build_event(event: dict, new_dt: datetime, med_id: str) -> dict:
    """Build a medlog event dict from an Apple item (dry-run, not written).

    Follows cmd_log's write path so imported events are indistinguishable from
    native ones: the slot comes from pick_slot (a weekly/prn med gets its ""
    slot, a daily med gets the slot nearest its time), logged_at is the system
    clock, and date/time carry the Apple event's own local moment. A dose absent
    in the Apple item is stored as None (the item said none), never the
    registered default. No dry_run key is emitted on applied events.
    """
    med = load_registry()["medications"].get(med_id, {})
    day, slot = attribute(med, new_dt)
    time_text = new_dt.strftime("%H:%M")
    return {
        "id": f"apple-{event['external_id']}",
        "logged_at": now().isoformat(),
        "med": med_id,
        "date": day,
        "slot": slot,
        "status": event["status"],
        # A "count" dose is one dose of the medication's configured current dose;
        # with no configured dose the amount stays unknown. Never "1 count".
        "dose": med.get("dose") if _is_count(event, med) else (event["dose"] if event.get("dose") is not None else None),
        "unit": (med.get("unit") if med.get("dose") is not None else None) if _is_count(event, med) else (event.get("unit") or None),
        "time": time_text,
        "time_source": "user" if time_text else "unspecified",
        "note": f"apple:{event['external_id']}",
        "apple_id": event["external_id"],
        "source": "apple_health",
    }


def cmd_import_apple(args) -> int:
    """Import an Apple Health medication export. Returns exit code."""
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"medlog import-apple: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, list):
        print("medlog import-apple: input is not a JSON list", file=sys.stderr)
        return 2

    the_map = load_map()
    existing = []
    for mid in load_registry()["medications"]:
        try:
            existing.extend(store_for(mid).events())
        except MedlogError:
            pass

    applied = []
    corrections = []  # (old event, new Apple event): Health time wins
    superseded = set()
    seen_ids = set()
    counts = {"imported": 0, "duplicates": 0, "ignored": 0, "unmapped": 0, "mismatches": 0, "corrected": 0}
    unmapped_names = []

    for item in data:
        if not isinstance(item, dict) or "external_id" not in item:
            continue
        ext = item["external_id"]
        if not isinstance(ext, str):
            continue
        status = classify_status(item.get("status", ""))
        if status == "ignored":
            counts["ignored"] += 1
            continue
        med_id = name_to_id(item.get("medication", ""), the_map)
        if not med_id and item.get("medication_id"):
            # The app may send the concept id instead of a display name; the map keys those "concept <id>".
            med_id = name_to_id(f"concept {item['medication_id']}", the_map)
        if not med_id or med_id not in load_registry()["medications"]:
            counts["unmapped"] += 1
            unmapped_names.append(item.get("medication", ""))
            continue
        new_dt = local_dt(item["start"])
        live = [e for e in existing if e["id"] not in superseded]
        is_dup, _ = duplicate_check(item, live + applied, new_dt, med_id)
        target = None if item["external_id"] in seen_ids else correction_target(item, live, new_dt, med_id)
        if target is not None and not any(_is_apple(e) and e.get("note") == f"apple:{ext}" for e in live):
            seen_ids.add(ext)
            new_event = build_event(item, new_dt, med_id)
            if new_event["dose"] is None and target.get("dose") is not None:
                # Apple stated no amount ("1 count" with no configured dose): keep what was recorded, only the time changes.
                new_event["dose"], new_event["unit"] = target["dose"], target.get("unit")
            corrections.append((target, new_event))
            superseded.add(target["id"])
            applied.append(new_event)
            continue
        is_dup, is_mismatch = duplicate_check(item, live + applied, new_dt, med_id)
        if is_dup:
            counts["duplicates"] += 1
            if is_mismatch:
                counts["mismatches"] += 1
            continue
        if ext in seen_ids:
            counts["duplicates"] += 1
            continue
        seen_ids.add(ext)
        applied.append(build_event(item, new_dt, med_id))

    counts["corrected"] = len(corrections)
    counts["imported"] = len(applied) - len(corrections)

    auto = getattr(args, "auto", False)
    held = None
    if auto:
        limit = int(os.environ.get("MEDLOG_AUTO_MAX_CHANGES", "12"))
        if counts["mismatches"]:
            held = "dose_mismatch"
        elif counts["imported"] + counts["corrected"] > limit:
            held = "too_many_changes"
    do_apply = args.apply or (auto and held is None)

    if do_apply:
        for event in applied:
            store_for(event["med"]).append(event)
        # Void after the new record is safely written, so a failure never leaves the dose unrecorded.
        for old, new in corrections:
            void_event(old["id"], f"Health app time wins: was {old.get('time') or 'no time'}, "
                                   f"now {new['time']} (import {new['id']})")

    names = ", ".join(sorted(set(unmapped_names)))
    print(f"import-apple: imported={counts['imported']} duplicates={counts['duplicates']} "
          f"ignored={counts['ignored']} unmapped={counts['unmapped']} "
          f"mismatches={counts['mismatches']} ({'applied' if do_apply else 'dry-run'}) "
          f"corrected={counts['corrected']}" + (f" held={held}" if held else ""))
    if auto and (counts["imported"] or counts["corrected"] or held):
        # Counts only, never names or doses.
        with open(data_dir() / "auto-import.log", "a", encoding="utf-8") as log:
            log.write(f"{now().isoformat()} file={Path(args.file).name} imported={counts['imported']} "
                      f"corrected={counts['corrected']} duplicates={counts['duplicates']} "
                      f"mismatches={counts['mismatches']} result={'held:' + held if held else 'applied'}\n")
    for old, new in corrections:
        print(f"correct: {new['med']} {new['date']} {new['slot'] or ''} {old.get('time') or 'no time'} -> {new['time']}")
    if names:
        print(f"unmapped: {names}")
    return 0
