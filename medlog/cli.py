"""medlog - deterministic medication log for any medication.

Records what the user says they took (or skipped) and answers "what is missing?".
It never guesses: the logging time is always the system clock (`logged_at`); the
dose TIME is only stored when the user supplied it (`--time`/`--at`), otherwise it
is null and `time_source` is "unspecified". It gives no dosing or interaction advice.

Commands:
  add-med ID --name N [--dose D --unit U] [--kind daily|weekly|prn] [--time HH:MM ...]
          [--weekday fri] [--start YYYY-MM-DD] [--store jsonl|v1] [--replace]
  list-meds | stop-med ID [--date D] | remove-med ID
  log ID [--status taken|skipped|missed|extra] [--time HH:MM | --at ISO] [--date D]
         [--slot S] [--dose D --unit U] [--note T] [--source S] [--force] [--dry-run]
  import-apple FILE [--apply | --auto]
  import-bridge [--db PATH] [--since DATE] [--apply | --auto]   (HealthRelay receiver database, read-only)
  doctor                                                       (setup check; exit 0 ok, 1 warning, 2 error)
  unmapped [--days N] | map-apple KEY ID [--replace] | check-med ID     (registering a new medication)
  void EVENT_ID --reason T
  show [--med ID] [--days N] [--format text|json|csv]
  missing [--med ID] [--days N]
  status
  untimed [--days N] [--med ID]

Settings: environment variable, then the config file ($MEDLOG_CONFIG or ~/.config/medlog/config.json), then the
default. MEDLOG_HOME / "data_dir" (default $XDG_DATA_HOME/medlog), MEDLOG_TZ / "timezone" (default: the system
zone), MEDLOG_STORE (store override, tests), MEDLOG_NOW (ISO, for tests). Every write takes a file lock.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys

from . import __version__
from .core import (STATUSES, MedlogError, cmd_add_med, cmd_log, cmd_remove_med, cmd_show, cmd_stop, cmd_untimed,
                   load_registry, missing_for_registry, status_for_registry, void_event)


def fmt_event(e: dict) -> str:
    dose = f" {e['dose']:g}{e['unit'] or ''}" if isinstance(e.get("dose"), (int, float)) else ""
    when = e["time"] or "time not given"
    return f"{e['date']} {e['slot'] or '':9} {e['med']}{dose} {e['status']} ({when}) id={e['id'][:8]}"


def emit(result, args) -> None:
    if getattr(args, "format", "text") == "json" or args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.cmd == "show" and getattr(args, "format", "text") == "csv":
        cols = ["date", "slot", "med", "status", "dose", "unit", "time", "time_source", "note", "source", "logged_at", "id"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result)
        print(buf.getvalue(), end="")
    elif args.cmd == "show":
        print("\n".join(fmt_event(e) for e in result) or "no records in that period")
    elif args.cmd == "missing":
        print("\n".join(f"MISSING {m['date']} {m['slot']:9} {m['med']} ({m['name']})" for m in result) or "nothing missing")
    elif args.cmd == "log":
        print(("DRY RUN, not written: " if result["dry_run"] else "logged: ") + fmt_event(result))
    elif args.cmd == "status":
        print(f"today {result['date']}: {len(result['recorded'])} recorded, {len(result['due_and_missing'])} due and not recorded")
        for m in result["due_and_missing"]:
            print(f"  missing {m['slot']:9} {m['med']} ({m['name']})")
    elif args.cmd == "list-meds":
        for mid, m in sorted(result.items()):
            when = ", ".join(m["times"]) or (m["weekday"] or "as needed")
            end = f" ended {m['end']}" if m.get("end") else ""
            print(f"{mid}: {m['name']} {m['dose'] or ''}{m['unit'] or ''} {m['kind']} {when}{end}")
    elif args.cmd == "untimed":
        print("\n".join(f"{e['date']} {e['med']} {e['status']}" for e in result) or "no untimed doses")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


def _version() -> str:
    return __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="medlog", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--version", action="version", version=f"medlog {_version()}")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-med")
    a.add_argument("id"); a.add_argument("--name", required=True); a.add_argument("--dose", type=float)
    a.add_argument("--unit"); a.add_argument("--kind", choices=["daily", "weekly", "prn"], default="daily")
    a.add_argument("--time", action="append"); a.add_argument("--weekday"); a.add_argument("--start")
    a.add_argument("--replace", action="store_true")
    a.add_argument("--store", default="jsonl",
                   help="which store this medication's doses live in (default jsonl; others come from extensions)")
    sub.add_parser("list-meds")
    s = sub.add_parser("stop-med"); s.add_argument("id"); s.add_argument("--date")
    rm = sub.add_parser("remove-med"); rm.add_argument("id")
    lg = sub.add_parser("log")
    lg.add_argument("med"); lg.add_argument("--status", choices=STATUSES, default="taken")
    lg.add_argument("--time"); lg.add_argument("--at"); lg.add_argument("--date"); lg.add_argument("--slot")
    lg.add_argument("--dose", type=float); lg.add_argument("--unit"); lg.add_argument("--note")
    lg.add_argument("--source", default="cli"); lg.add_argument("--force", action="store_true")
    lg.add_argument("--dry-run", action="store_true")
    imp = sub.add_parser("import-apple", help="import an Apple Health medication export")
    imp.add_argument("file"); imp.add_argument("--apply", action="store_true")
    imp.add_argument("--auto", action="store_true", help="apply by itself only when clean (no dose mismatch) and small; else dry run")
    ib = sub.add_parser("import-bridge", help="import medication dose events from a HealthRelay receiver database (read-only)")
    ib.add_argument("--db", help="path to the receiver SQLite database (default: the bridge_db setting)")
    ib.add_argument("--since", help="re-read events that started on/after YYYY-MM-DD (backfill; ignores the cursor)")
    ib.add_argument("--apply", action="store_true", help="write the records (default is a dry-run preview)")
    ib.add_argument("--auto", action="store_true", help="unattended: apply only when clean and small, else dry run")
    um = sub.add_parser("unmapped", help="Apple names/concept ids in recent exports that map to nothing (read-only)")
    um.add_argument("--days", type=int, default=14)
    um.add_argument("--db", help="receiver database to read (default: the bridge_db setting, else the export files)")
    ma = sub.add_parser("map-apple", help="map an Apple display name or 'concept <id>' to a registry id")
    ma.add_argument("key"); ma.add_argument("id"); ma.add_argument("--replace", action="store_true")
    cm = sub.add_parser("check-med", help="is a registered medication set up end to end? (read-only; exit 2 if not)")
    cm.add_argument("id")
    v = sub.add_parser("void"); v.add_argument("event_id"); v.add_argument("--reason", required=True)
    sh = sub.add_parser("show"); sh.add_argument("--med"); sh.add_argument("--days", type=int, default=7)
    sh.add_argument("--format", choices=["text", "json", "csv"], default="text")
    m = sub.add_parser("missing"); m.add_argument("--med"); m.add_argument("--days", type=int, default=7)
    sub.add_parser("status")
    sub.add_parser("doctor", help="check the setup: config, data dir, bridge database, last import, unmapped (exit 0 ok, 1 warning, 2 error)")
    u = sub.add_parser("untimed"); u.add_argument("--days", type=int, default=30); u.add_argument("--med")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "add-med":
            result = cmd_add_med(args)
        elif args.cmd == "list-meds":
            result = load_registry()["medications"]
        elif args.cmd == "stop-med":
            result = cmd_stop(args)
        elif args.cmd == "log":
            result = cmd_log(args)
        elif args.cmd == "import-apple":
            from .apple import cmd_import_apple as _cmd_import_apple  # lazy: avoids circular import
            return _cmd_import_apple(args)
        elif args.cmd == "import-bridge":
            from .bridge import cmd_import_bridge  # lazy
            return cmd_import_bridge(args)
        elif args.cmd == "doctor":
            from .doctor import cmd_doctor  # lazy
            return cmd_doctor(args)
        elif args.cmd in ("unmapped", "map-apple", "check-med"):
            from . import setup_cmds  # lazy: setup_cmds imports core and apple
            return {"unmapped": setup_cmds.cmd_unmapped, "map-apple": setup_cmds.cmd_map_apple,
                    "check-med": setup_cmds.cmd_check_med}[args.cmd](args)
        elif args.cmd == "void":
            result = void_event(args.event_id, args.reason)
        elif args.cmd == "show":
            result = cmd_show(args)
        elif args.cmd == "missing":
            result = missing_for_registry(args.med, args.days)
        elif args.cmd == "remove-med":
            result = cmd_remove_med(args)
        elif args.cmd == "untimed":
            result = cmd_untimed(args)
        else:  # status
            result = status_for_registry()
        emit(result, args)
        return 0
    except MedlogError as exc:
        print(f"medlog: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
