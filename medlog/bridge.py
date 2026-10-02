"""import-bridge: read HealthRelay medication dose events straight from the receiver's SQLite database.

The database is opened read-only (never ``immutable``: the receiver writes to it while we read). Rows become the
records ``import-apple`` already consumes and are handed to it unchanged, so matching, duplicate detection, the
"Health time wins" correction and the unattended-import holds are exactly the ones import-apple has. This module adds
no dose logic.

The receiver database is the durable source, so no export files are needed. A cursor (the newest ``updated_at`` seen)
lives in ``bridge-state.json`` in the data directory and moves forward ONLY after a successful apply, or when
there was nothing to apply. A held import leaves it alone, so the next run sees the same rows. Events that map to no
medication do not block the cursor; re-read them with ``--since DATE`` once they are mapped (duplicates are skipped by
``external_id``).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import config
from .core import MedlogError, data_dir, locked, now, tz
from .sqlite_retry import is_transient, retry_read

TABLE = "medication_dose_events"
REQUIRED = ("client_record_id", "medication_name", "status", "start_time", "updated_at")
OPTIONAL = ("medication_concept_key", "status_raw", "scheduled_time", "dose", "unit")
STATE_NAME = "bridge-state.json"


def resolve_db(db: str | None) -> str:
    """The --db override, else the `bridge_db` setting; an error that says where to set it when neither exists."""
    if db:
        return db
    try:
        configured = config.bridge_db_setting()
    except config.ConfigError as exc:
        raise MedlogError(str(exc)) from exc
    if not configured:
        raise MedlogError(f"no receiver database: pass --db or set 'bridge_db' in {config.config_path()} (or MEDLOG_BRIDGE_DB)")
    return configured


def open_readonly(db: str) -> sqlite3.Connection:
    path = os.path.abspath(os.path.expanduser(db))
    if not os.path.isfile(path):
        raise MedlogError(f"receiver database not found: {db}")
    if os.path.getsize(path) == 0:
        raise MedlogError(f"receiver database is a 0-byte file, not a database: {db}")
    uri = "file:" + urllib.parse.quote(path) + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.OperationalError as exc:
        if is_transient(exc):
            raise
        raise MedlogError(f"cannot open the receiver database read-only: {exc}") from exc


def _local(iso_text):
    if iso_text is None:
        return None
    when = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
    return when.astimezone(tz())


def _read_rows(db: str, cursor: str | None):
    con = open_readonly(db)
    try:
        cur = con.cursor()
        try:
            columns = {row[1] for row in cur.execute(f"PRAGMA table_info({TABLE})")}
        except sqlite3.DatabaseError as exc:
            if is_transient(exc):
                raise
            raise MedlogError(f"cannot read the receiver database: {exc}") from exc
        if not columns:
            return None, None  # an older receiver without the table: nothing to import, cursor untouched
        missing = [c for c in REQUIRED if c not in columns]
        if missing:
            raise MedlogError(f"receiver table {TABLE} lacks required column(s): {', '.join(missing)}")
        select = ", ".join(c if c in columns else f"NULL AS {c}" for c in REQUIRED + OPTIONAL)
        where, params = [], []
        if cursor:
            where.append("updated_at > ?")
            params.append(cursor)
        sql = f"SELECT {select} FROM {TABLE}" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY start_time ASC"
        return cur.execute(sql, params).fetchall(), REQUIRED + OPTIONAL
    finally:
        con.close()


def fetch_records(db: str, cursor: str | None = None, since: str | None = None):
    """Return (records, newest_updated_at). ``cursor`` filters on updated_at, ``since`` (a local date in the configured zone) on the event start.

    The whole read (open, first query, fetch) is retried while the receiver is writing (see sqlite_retry)."""
    try:
        rows, names = retry_read(lambda: _read_rows(db, cursor))
    except sqlite3.OperationalError as exc:
        raise MedlogError(f"ERROR: {db} unreadable ({exc})") from exc
    if rows is None:
        return [], None
    records, newest = [], None
    for row in rows:
        item = dict(zip(names, row))
        start, scheduled = _local(item["start_time"]), _local(item["scheduled_time"])
        if since and (start is None or start.date().isoformat() < since):
            continue  # ``since`` is a date in the configured zone, not a UTC string
        records.append({
            "external_id": item["client_record_id"], "medication": item["medication_name"],
            "medication_id": item["medication_concept_key"], "status": item["status"], "status_raw": item["status_raw"],
            "start": start.isoformat() if start else None, "scheduled": scheduled.isoformat() if scheduled else None,
            "dose": item["dose"], "unit": item["unit"],
        })
        if item["updated_at"] and (newest is None or item["updated_at"] > newest):
            newest = item["updated_at"]
    return records, newest


def _state_path() -> Path:
    return data_dir() / STATE_NAME


def load_cursor() -> str | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("last_updated_at")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def save_cursor(value: str) -> None:
    path = _state_path()
    with locked(path):
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"last_updated_at": value}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)


def cmd_import_bridge(args) -> int:
    from .apple import cmd_import_apple
    since = args.since
    if since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        raise MedlogError(f"bad --since {since!r}, use YYYY-MM-DD")
    cursor = None if since else load_cursor()
    records, newest = fetch_records(resolve_db(args.db), cursor=cursor, since=since)
    print(f"import-bridge: events={len(records)} since={since or cursor or 'none'}")
    if not records:
        return 0
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    tmp = data / f"bridge-{now().strftime('%Y%m%d_%H%M%S')}.json"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(records, handle)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = cmd_import_apple(SimpleNamespace(file=str(tmp), apply=args.apply, auto=args.auto))
    finally:
        tmp.unlink(missing_ok=True)
    text = buffer.getvalue()
    sys.stdout.write(text)
    first = text.splitlines()[0] if text else ""
    applied = "(applied)" in first
    held = " held=" in first
    if code == 0 and applied and not held and newest and (args.apply or args.auto):
        existing = load_cursor()
        if not existing or newest > existing:  # only ever forward, also after a --since backfill
            save_cursor(newest)
    return code
