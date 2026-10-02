"""medlog doctor: a read-only check of the setup, so a wrong path shows up as an error instead of "nothing to import".

Reports the config file used, the data directory, the receiver database (`bridge_db`: exists, not empty, opens
read-only, has the dose-event table), the time of the last import and the number of unmapped Apple medication names.
Exit codes: 0 ok, 1 warning (database not configured, stale, unmapped names), 2 error (configured database missing, empty or unreadable).
Counts and paths only: no medication names or doses are printed.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

from . import config
from .bridge import TABLE, open_readonly
from .sqlite_retry import retry_read
from .core import MedlogError, data_dir, now

STALE_DAYS = 3


def _last_import(data: "os.PathLike") -> str | None:
    log = data / "auto-import.log"
    try:
        lines = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        match = re.match(r"(\d{4}-\d{2}-\d{2}T[\d:.]+(?:[+-]\d{2}:\d{2})?)", line)
        if match:
            return match.group(1)
    return None


def _check_bridge(report: dict) -> None:
    try:
        db = config.bridge_db_setting()
    except config.ConfigError as exc:
        report["errors"].append(str(exc))
        return
    report["bridge_db"] = {"path": db}
    if not db:
        report["warnings"].append(f"bridge_db is not set; import-bridge and unmapped need it (add it to {config.config_path()} or set MEDLOG_BRIDGE_DB)")
        return
    path = os.path.abspath(os.path.expanduser(db))
    info = report["bridge_db"]
    info["exists"] = os.path.isfile(path)
    if not info["exists"]:
        report["errors"].append(f"{db} not found")
        return
    info["bytes"] = os.path.getsize(path)
    if info["bytes"] == 0:
        report["errors"].append(f"{db} is a 0-byte file, not a database")
        return
    try:
        def probe():
            con = open_readonly(path)
            try:
                return bool(con.execute(
                    "select 1 from sqlite_master where type='table' and name=?", (TABLE,)).fetchone())
            finally:
                con.close()
        info["has_dose_event_table"] = retry_read(probe)
        info["opens_read_only"] = True
    except (MedlogError, sqlite3.Error) as exc:
        info["opens_read_only"] = False
        report["errors"].append(f"{db} unreadable ({exc.__class__.__name__})")
        return
    if not info["has_dose_event_table"]:
        report["warnings"].append(f"{db} has no {TABLE} table (receiver too old, or not a receiver database)")
    age = now() - datetime.fromtimestamp(os.path.getmtime(path), now().tzinfo)
    info["days_since_modified"] = age.days
    if age > timedelta(days=STALE_DAYS):
        report["warnings"].append(f"{db} not modified for {age.days} days (no phone sync?)")


def build_report() -> dict:
    report: dict = {"errors": [], "warnings": []}
    try:
        path = config.config_path()
        report["config_file"] = {"path": str(path), "exists": path.is_file()}
        report["data_dir"] = {"path": str(data_dir())}
        report["data_dir"]["exists"] = os.path.isdir(report["data_dir"]["path"])
    except (MedlogError, config.ConfigError) as exc:
        report["errors"].append(str(exc))
        return report
    if not report["data_dir"]["exists"]:
        report["errors"].append(f"data dir {report['data_dir']['path']} not found")
    _check_bridge(report)
    report["last_import"] = _last_import(data_dir())
    if not report["errors"] and report.get("bridge_db", {}).get("has_dose_event_table"):
        from .setup_cmds import unmapped_rows
        rows = unmapped_rows(SimpleNamespace(days=14, db=None))
        report["unmapped_names_14d"] = len(rows)
        if rows:
            report["warnings"].append(f"{len(rows)} Apple medication name(s) map to nothing (medlog unmapped)")
    return report


def cmd_doctor(args) -> int:
    report = build_report()
    code = 2 if report["errors"] else 1 if report["warnings"] else 0
    report["status"] = {0: "ok", 1: "warning", 2: "error"}[code]
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    cfg = report.get("config_file", {})
    print(f"config file: {cfg.get('path')} ({'found' if cfg.get('exists') else 'not found, using defaults'})")
    print(f"data dir: {report.get('data_dir', {}).get('path')} ({'found' if report.get('data_dir', {}).get('exists') else 'MISSING'})")
    bridge = report.get("bridge_db", {})
    if bridge:
        print(f"bridge db: {bridge.get('path')}")
        for key in ("exists", "bytes", "opens_read_only", "has_dose_event_table", "days_since_modified"):
            if key in bridge:
                print(f"  {key}: {bridge[key]}")
    print(f"last import: {report.get('last_import') or 'none recorded'}")
    if "unmapped_names_14d" in report:
        print(f"unmapped names (14 days): {report['unmapped_names_14d']}")
    for err in report["errors"]:
        print(f"ERROR: {err}")
    for warn in report["warnings"]:
        print(f"WARNING: {warn}")
    print(f"status: {report['status']}")
    return code
