"""apple_setup - helpers for registering a new medication: `unmapped`, `map-apple`, `check-med`.

Config plumbing only. `unmapped` and `check-med` are read-only; `map-apple` edits the Apple name map
(``apple_med_map.json``, a config file, never a medication log). Nothing here changes dosing, schedules,
statuses, duplicate or correction logic, and nothing infers a value: the user supplies every key and id.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date as Date, timedelta
from pathlib import Path

from .core import MedlogError, data_dir, expected_slots, load_registry, locked, now, store_for
from .apple import _is_apple, classify_status, map_path

BACKUPS_KEPT = 10


def _raw_map() -> dict:
    path = map_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MedlogError(f"apple map is not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise MedlogError(f"apple map is not a JSON object: {path}")
    return raw


def _lower_map(raw: dict) -> dict[str, str]:
    return {k.strip().lower(): v.strip() for k, v in raw.items() if k and isinstance(v, str) and v.strip()}


def _sources(args, since: str):
    """Lists of dose-event records: the receiver database when --db is given, else each retained export file."""
    if getattr(args, "db", None):
        from .bridge import fetch_records
        yield fetch_records(args.db, since=since)[0]
        return
    for path in sorted((data_dir() / "bridge-exports").glob("bridge-meds-*.json")):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def _emit(args, result, text: str) -> None:
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else text)


# ---------------------------------------------------------------- unmapped
def cmd_unmapped(args) -> int:
    """Names and concept ids in recent bridge exports that reach no registered medication (read-only)."""
    the_map = _lower_map(_raw_map())
    registry = load_registry()["medications"]
    since = (now().date() - timedelta(days=args.days)).isoformat()
    groups: dict[tuple[str, str], dict] = {}
    for items in _sources(args, since):
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or classify_status(item.get("status", "")) == "ignored":
                continue
            day = str(item.get("start") or "")[:10]
            if day < since:
                continue
            name = str(item.get("medication") or "").strip()
            concept = str(item.get("medication_id") or "").strip()
            target = the_map.get(name.lower()) if name else None
            if not target and concept:
                target = the_map.get(f"concept {concept}".lower())
            if target and target in registry:
                continue
            group = groups.setdefault((name, concept), {"name": name, "concept": f"concept {concept}" if concept else "",
                                                        "events": 0, "newest": ""})
            group["events"] += 1
            group["newest"] = max(group["newest"], day)
    rows = sorted(groups.values(), key=lambda r: (r["name"].lower(), r["concept"]))
    lines = [f"{r['name'] or '(no name)'} | {r['concept'] or '(no concept id)'} | {r['events']} events, newest {r['newest']}"
             for r in rows]
    _emit(args, rows, "\n".join(lines) if lines else f"nothing unmapped in the last {args.days} days")
    return 0


# --------------------------------------------------------------- map-apple
def cmd_map_apple(args) -> int:
    """Add KEY (display name or `concept <id>`) -> ID to the Apple map, with a timestamped backup."""
    key, med_id = args.key.strip(), args.id.strip()
    if not key:
        raise MedlogError("map-apple needs a non-empty KEY (a display name or 'concept <id>')")
    if med_id not in load_registry()["medications"]:
        raise MedlogError(f"{med_id!r} is not in the registry; register it first with add-med")
    path = map_path()
    with locked(path):
        raw = _raw_map()
        clash = [k for k in raw if k.strip().lower() == key.lower()]
        if clash and not args.replace:
            raise MedlogError(f"key {clash[0]!r} is already mapped to {raw[clash[0]]!r}; pass --replace to change it")
        if path.exists():
            stamp = now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
            for old in sorted(path.parent.glob(f"{path.name}.bak-*"))[:-BACKUPS_KEPT]:
                old.unlink()
        for k in clash:
            del raw[k]
        raw[key] = med_id
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    _emit(args, {"key": key, "id": med_id, "replaced": bool(clash)}, f"mapped {key!r} -> {med_id}" + (" (replaced)" if clash else ""))
    return 0


# --------------------------------------------------------------- check-med
def cmd_check_med(args) -> int:
    """Is a registered medication set up end to end? Exit 0 when complete, 2 with one line per problem."""
    reg = load_registry()["medications"]
    med = reg.get(args.id)
    problems, facts = [], []
    if med is None:
        problems.append(f"{args.id}: not in the registry (add-med)")
        _report(args, problems, facts)
        return 2
    kind = med.get("kind")
    keys = sorted(k for k, v in _lower_map(_raw_map()).items() if v == args.id)
    if keys:
        facts.append(f"{len(keys)} Apple map key(s)")
    else:
        problems.append(f"{args.id}: no Apple map key points to it (medlog unmapped, then map-apple)")
    try:
        imported = [e for e in store_for(args.id).events() if not e["voided"] and e["med"] == args.id and _is_apple(e)]
    except MedlogError as exc:
        imported = []
        problems.append(f"{args.id}: cannot read its store ({exc})")
    if imported:
        facts.append(f"newest Apple import {max(e['date'] for e in imported)}")
    else:
        problems.append(f"{args.id}: no Apple import yet")
    if kind == "prn":
        facts.append("as needed, so missing does not cover it (expected)")
    else:
        today = now().date()
        if any(expected_slots(med, today + timedelta(days=d)) for d in range(7)):
            facts.append("covered by missing")
        else:
            problems.append(f"{args.id}: has no expected slots, so missing cannot cover it (times or weekday)")
        if med.get("end"):
            facts.append(f"stopped {med['end']}")
    _report(args, problems, facts)
    return 2 if problems else 0


def _report(args, problems: list[str], facts: list[str]) -> None:
    result = {"id": args.id, "complete": not problems, "problems": problems, "facts": facts}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif problems:
        print("\n".join(problems))
    else:
        print(f"{args.id}: complete ({'; '.join(facts)})")
