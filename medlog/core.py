"""medlog core: registry, stores, logging, void, missing, status.

Deterministic medication log. It never guesses: the logging time is always the system clock (`logged_at`); the dose
TIME is only stored when the user supplied it, otherwise it is null and `time_source` is "unspecified". It gives no
dosing or interaction advice. Settings come from medlog.config (environment, then config file, then defaults).
"""
from __future__ import annotations

import fcntl
import importlib
import json
import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
STATUSES = ["taken", "skipped", "missed", "extra"]


class MedlogError(Exception):
    code = 2


class DuplicateError(MedlogError):
    code = 3


def _setting(fn):
    try:
        return fn()
    except config.ConfigError as exc:
        raise MedlogError(str(exc)) from exc


def tz() -> ZoneInfo:
    return ZoneInfo(_setting(config.timezone_name))


def now() -> datetime:
    raw = os.environ.get("MEDLOG_NOW", "").strip()
    if raw:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz())
    return datetime.now(tz())


def data_dir() -> Path:
    return Path(_setting(config.data_dir_setting)).expanduser()


def parse_date(text: str) -> Date:
    try:
        return Date.fromisoformat(text)
    except ValueError as exc:
        raise MedlogError(f"bad date {text!r}, use YYYY-MM-DD") from exc


def parse_hhmm(text: str) -> str:
    try:
        hour, minute = text.split(":")
        hour, minute = int(hour), int(minute)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
    except ValueError as exc:
        raise MedlogError(f"bad time {text!r}, use HH:MM (24h, local time)") from exc
    return f"{hour:02d}:{minute:02d}"


def label_for(hhmm: str) -> str:
    hour = int(hhmm[:2])
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


def slots_for(med: dict) -> list[tuple[str, str]]:
    """[(slot, HH:MM)] - a slot is morning/afternoon/evening, or HH:MM when labels collide."""
    times = sorted(med.get("times") or [])
    labels = [label_for(t) for t in times]
    return [(lab if labels.count(lab) == 1 else t, t) for t, lab in zip(times, labels)]


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / ".medlog.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------- registry
def reg_path() -> Path:
    return data_dir() / "medications.json"


def load_registry() -> dict:
    path = reg_path()
    if not path.exists():
        return {"schema_version": 1, "medications": {}}
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MedlogError(f"registry is not valid JSON: {path}") from exc
    if not isinstance(reg, dict) or reg.get("schema_version") != 1 or not isinstance(reg.get("medications"), dict):
        raise MedlogError(f"registry has an unexpected format: {path}")
    return reg


def save_registry(reg: dict) -> None:
    path = reg_path()
    with locked(path):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


# ------------------------------------------------------------------ stores
class JsonlStore:
    """Append-only events.jsonl in MEDLOG_HOME; voids are separate lines."""

    def __init__(self) -> None:
        self.path = data_dir() / "events.jsonl"

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        events, voided = [], {}
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MedlogError(f"{self.path} line {number} is not valid JSON") from exc
            if item.get("type") == "void":
                voided[item["target"]] = item.get("reason", "")
            else:
                events.append(item)
        for event in events:
            event["voided"] = event["id"] in voided
        return events

    def append(self, event: dict) -> None:
        with locked(self.path):
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def void(self, event_id: str, reason: str) -> None:
        if not any(e["id"] == event_id and not e["voided"] for e in self.events()):
            raise MedlogError(f"no active event with id {event_id}")
        self.append({"type": "void", "target": event_id, "reason": reason, "logged_at": now().isoformat()})


STORE_FACTORIES = {}
_extensions_loaded = False


def register_store(name: str, factory) -> None:
    """Make a store available under `name` (called by extension modules; `jsonl` is built in)."""
    STORE_FACTORIES[name] = factory


def load_extensions() -> None:
    """Import the modules listed under `extensions` in the config file, once per process."""
    global _extensions_loaded
    if _extensions_loaded:
        return
    for module in _setting(config.extensions):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            raise MedlogError(f"cannot load extension {module!r}: {exc}") from exc
    _extensions_loaded = True


def available_stores() -> list[str]:
    load_extensions()
    return sorted(STORE_FACTORIES)


def store_for(med_id=None):
    """Return the store for a medication.

    A MEDLOG_STORE env var stays as a global override (used by tests).
    Otherwise each medication routes to its own store via the registry
    `store` field ("jsonl" default; other names come from extensions).
    """
    override = os.environ.get("MEDLOG_STORE")
    if override is None:
        reg = load_registry()
        med = reg["medications"].get(med_id) if med_id else None
        override = (med or {}).get("store", "jsonl")
    load_extensions()
    factory = STORE_FACTORIES.get(override)
    if factory is None:
        raise MedlogError(f"unknown store {override!r}; available: {', '.join(sorted(STORE_FACTORIES))}")
    return factory()


def open_store():
    return JsonlStore()


register_store("jsonl", JsonlStore)


# ------------------------------------------------------------------- logic
def active(events: list[dict], med: str | None = None) -> list[dict]:
    return [e for e in events if not e["voided"] and (med is None or e["med"] == med)]


def pick_slot(med: dict, events: list[dict], med_id: str, day: str, time_text: str | None) -> str | None:
    slots = slots_for(med)
    if med.get("kind") in ("prn", "weekly") or not slots:
        return None
    taken = {e["slot"] for e in active(events, med_id) if e["date"] == day}
    if time_text:
        minutes = int(time_text[:2]) * 60 + int(time_text[3:])
        return min(slots, key=lambda st: abs(int(st[1][:2]) * 60 + int(st[1][3:]) - minutes))[0]
    free = [s for s, _ in slots if s not in taken]
    if not free:
        raise DuplicateError(f"every scheduled slot for {med_id} on {day} already has a record; pass --slot or --force")
    return free[0]


def cmd_log(args) -> dict:
    reg = load_registry()
    med = reg["medications"].get(args.med)
    if not med:
        raise MedlogError(f"unknown medication {args.med!r}; add it first with add-med")
    current = now()
    time_text, day = None, args.date
    if args.at:
        try:
            when = datetime.fromisoformat(args.at)
        except ValueError as exc:
            raise MedlogError(f"bad --at {args.at!r}, use an ISO datetime") from exc
        when = when if when.tzinfo else when.replace(tzinfo=tz())
        when = when.astimezone(tz())
        time_text, day = when.strftime("%H:%M"), when.date().isoformat()
    elif args.time:
        time_text = parse_hhmm(args.time)
    day = day or current.date().isoformat()
    parse_date(day)
    if time_text and datetime.fromisoformat(f"{day}T{time_text}").replace(tzinfo=tz()) > current + timedelta(minutes=5):
        raise MedlogError("that time is in the future; nothing was logged")
    if Date.fromisoformat(day) > current.date():
        raise MedlogError("that date is in the future; nothing was logged")
    store = store_for(args.med)
    events = store.events()
    slot = args.slot or pick_slot(med, events, args.med, day, time_text)
    if args.status != "extra" and not args.force:
        clash = [e for e in active(events, args.med) if e["date"] == day and e["slot"] == slot and e["status"] != "extra"]
        if clash:
            raise DuplicateError(f"{args.med} {day} {slot or ''} already has a {clash[0]['status']} record (id {clash[0]['id']}); use --force to add another")
    event = {
        "id": str(uuid.uuid4()), "logged_at": current.isoformat(), "med": args.med, "date": day, "slot": slot,
        "status": args.status, "dose": args.dose if args.dose is not None else med.get("dose"),
        "unit": args.unit or med.get("unit"), "time": time_text,
        "time_source": "user" if time_text else "unspecified", "note": args.note, "source": args.source,
    }
    if not args.dry_run:
        store.append(event)
    event["dry_run"] = args.dry_run
    return event


def expected_slots(med: dict, day: Date) -> list[str]:
    if med.get("kind") == "prn":
        return []
    if med.get("kind") == "weekly":
        return [""] if WEEKDAYS[day.weekday()] == med.get("weekday") else []
    return [s for s, _ in slots_for(med)]


def missing(events: list[dict], reg: dict, med_id: str | None, days: int) -> list[dict]:
    current = now()
    out = []
    for mid, med in sorted(reg["medications"].items()):
        if med_id and mid != med_id:
            continue
        first = Date.fromisoformat(med.get("start") or med["added"][:10])
        end = Date.fromisoformat(med["end"]) if med.get("end") else None
        events = store_for(mid).events()
        recorded = {(e["date"], e["slot"] or "") for e in active(events, mid)}
        for back in range(days, -1, -1):
            day = current.date() - timedelta(days=back)
            if day < first or (end and day > end):
                continue
            for slot in expected_slots(med, day):
                when = dict(slots_for(med)).get(slot, slot)
                if day == current.date() and when and ":" in when and when > current.strftime("%H:%M"):
                    continue  # not due yet today
                if (day.isoformat(), slot) not in recorded:
                    out.append({"med": mid, "name": med["name"], "date": day.isoformat(), "slot": slot or "week"})
    return out


# --------------------------------------------------------------------- cli
def cmd_add_med(args) -> dict:
    reg = load_registry()
    if args.id in reg["medications"] and not args.replace:
        raise MedlogError(f"{args.id} already exists; pass --replace to overwrite it")
    if args.kind == "weekly" and args.weekday not in WEEKDAYS:
        raise MedlogError(f"weekly medications need --weekday one of {', '.join(WEEKDAYS)}")
    if args.kind == "daily" and not args.time:
        raise MedlogError("daily medications need at least one --time HH:MM (the expected dose times)")
    if args.store not in available_stores():
        raise MedlogError(f"unknown store {args.store!r}; available: {', '.join(available_stores())}")
    reg["medications"][args.id] = {
        "name": args.name, "dose": args.dose, "unit": args.unit, "kind": args.kind,
        "times": sorted({parse_hhmm(t) for t in args.time or []}),
        "weekday": args.weekday if args.kind == "weekly" else None,
        "start": args.start or now().date().isoformat(), "end": None, "added": now().isoformat(),
        "store": args.store,
    }
    save_registry(reg)
    return {args.id: reg["medications"][args.id]}


def cmd_stop(args) -> dict:
    reg = load_registry()
    if args.id not in reg["medications"]:
        raise MedlogError(f"unknown medication {args.id!r}")
    reg["medications"][args.id]["end"] = args.date or now().date().isoformat()
    save_registry(reg)
    return {args.id: reg["medications"][args.id]}


def cmd_show(args) -> list[dict]:
    since = (now().date() - timedelta(days=args.days)).isoformat()
    if args.med:
        events = store_for(args.med).events()
    else:
        reg = load_registry()
        events = []
        for mid in reg["medications"]:
            try:
                events.extend(store_for(mid).events())
            except MedlogError:
                pass
        events = list({e["id"]: e for e in events}.values())  # medications sharing a store read each event once
    rows = [e for e in active(events, args.med) if e["date"] >= since]
    return sorted(rows, key=lambda e: (e["date"], e["time"] or "", e["logged_at"] or ""))


def events_for_registry() -> list[dict]:
    """All active events across every store, sorted by date then time."""
    reg = load_registry()
    events = []
    for mid in reg["medications"]:
        try:
            events.extend(store_for(mid).events())
        except MedlogError:
            pass
    return list({e["id"]: e for e in events}.values())


def void_event(event_id: str, reason: str) -> dict:
    """Void an event in whichever store holds it. `event_id` is the full id or a unique prefix of at
    least 6 characters (the short id printed by `log`); an ambiguous prefix is refused."""
    reg = load_registry()
    hits = []  # (store, full id)
    for mid in reg["medications"]:
        try:
            store = store_for(mid)
        except MedlogError:
            continue
        for e in store.events():
            if e["voided"] or any(h[1] == e["id"] for h in hits):
                continue
            if e["id"] == event_id or (len(event_id) >= 6 and e["id"].startswith(event_id)):
                hits.append((store, e["id"]))
    exact = [h for h in hits if h[1] == event_id]
    hits = exact or hits
    if not hits:
        raise MedlogError(f"no active event with id {event_id}")
    if len(hits) > 1:
        raise MedlogError(f"id prefix {event_id} is ambiguous ({len(hits)} events); use more characters")
    store, full = hits[0]
    store.void(full, reason)
    return {"voided": full, "reason": reason}


def missing_for_registry(med_id: str | None, days: int) -> list[dict]:
    reg = load_registry()
    events = events_for_registry()
    return missing(events, reg, med_id, days)


def status_for_registry() -> dict:
    reg = load_registry()
    events = events_for_registry()
    today = now().date().isoformat()
    return {"date": today,
            "recorded": [e for e in active(events) if e["date"] == today],
            "due_and_missing": [m for m in missing(events, reg, None, 0) if m["date"] == today]}


def cmd_remove_med(args) -> dict:
    """Remove a medication from the registry after backing it up.

    Refuses when any event in any store still references the id.
    """
    reg = load_registry()
    if args.id not in reg["medications"]:
        raise MedlogError(f"unknown medication {args.id!r}")
    for mid, med in reg["medications"].items():
        try:
            store = store_for(mid)
        except MedlogError:
            continue
        if any(e["med"] == args.id and not e.get("voided") for e in store.events()):
            raise MedlogError(f"{args.id} still referenced by an event in {mid}'s store")
    removed = reg["medications"].pop(args.id)
    path = reg_path()
    stamp = now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    save_registry(reg)
    return {"removed": args.id, "backup": str(backup), "detail": removed}


def cmd_untimed(args) -> list[dict]:
    """List doses without a time (date, medication id and status only)."""
    since = (now().date() - timedelta(days=args.days)).isoformat()
    rows = []
    seen = set()
    for e in events_for_registry():
        if e["date"] >= since and not e["voided"] and e.get("time") is None:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            rows.append(e)
    rows.sort(key=lambda e: (e["date"], e["med"]))
    return rows


