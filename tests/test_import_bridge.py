"""import-bridge against a synthetic receiver database. FAKE data only."""
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import medlog

ENV = ("MEDLOG_CONFIG", "MEDLOG_HOME", "MEDLOG_TZ", "MEDLOG_NOW", "MEDLOG_STORE", "MEDLOG_AUTO_MAX_CHANGES")
COLS = ("client_record_id TEXT, medication_name TEXT, medication_concept_key TEXT, status TEXT, status_raw TEXT, "
        "start_time TEXT, scheduled_time TEXT, dose REAL, unit TEXT, updated_at TEXT")


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = {k: os.environ.get(k) for k in ENV}
        self.home = Path(self.tmp.name) / "data"
        os.environ.update(MEDLOG_CONFIG=str(Path(self.tmp.name) / "none.json"), MEDLOG_HOME=str(self.home),
                          MEDLOG_TZ="UTC", MEDLOG_NOW="2030-01-15T12:00:00", MEDLOG_STORE="jsonl")
        os.environ.pop("MEDLOG_AUTO_MAX_CHANGES", None)
        self.addCleanup(self.restore)
        self.db = str(Path(self.tmp.name) / "receiver.sqlite")
        con = sqlite3.connect(self.db)
        con.execute(f"CREATE TABLE medication_dose_events ({COLS})")
        con.commit()
        con.close()
        self.cli("add-med", "fk", "--name", "Fakepril", "--dose", "10", "--unit", "mg", "--time", "08:00", "--start", "2030-01-10")
        self.cli("add-med", "fk2", "--name", "Fakelex", "--dose", "5", "--unit", "mg", "--time", "09:00", "--start", "2030-01-10")
        (self.home / "apple_med_map.json").write_text(json.dumps({"Fakepril": "fk", "concept 111": "fk2"}))

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = medlog.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def add_event(self, ext, name, day, hour, updated, concept=None, dose=10.0, status="taken"):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO medication_dose_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ext, name, concept, status, status, f"{day}T{hour}:00:00Z", None, dose, "mg", updated))
        con.commit()
        con.close()

    def events(self):
        p = self.home / "events.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def cursor(self):
        p = self.home / "bridge-state.json"
        return json.loads(p.read_text())["last_updated_at"] if p.exists() else None

    def sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_default_is_a_dry_run_and_never_touches_the_database(self):
        self.add_event("e1", "Fakepril", "2030-01-14", "08", "2030-01-14T09:00:00Z")
        before = self.sha(self.db)
        code, out, _ = self.cli("import-bridge", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("events=1", out)
        self.assertIn("dry-run", out)
        self.assertEqual(self.events(), [])
        self.assertIsNone(self.cursor())
        self.assertEqual(self.sha(self.db), before)

    def test_apply_imports_advances_the_cursor_and_rerun_is_empty(self):
        self.add_event("e1", "Fakepril", "2030-01-14", "08", "2030-01-14T09:00:00Z")
        before = self.sha(self.db)
        code, out, _ = self.cli("import-bridge", "--db", self.db, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("imported=1", out)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.cursor(), "2030-01-14T09:00:00Z")
        self.assertEqual(self.sha(self.db), before)
        _, out, _ = self.cli("import-bridge", "--db", self.db, "--apply")
        self.assertIn("events=0", out)
        self.assertEqual(len(self.events()), 1)

    def test_backfill_since_is_duplicate_safe_and_never_moves_the_cursor_back(self):
        self.add_event("e1", "Fakepril", "2030-01-13", "08", "2030-01-13T09:00:00Z")
        self.add_event("e2", "Fakepril", "2030-01-14", "08", "2030-01-14T09:00:00Z")
        self.cli("import-bridge", "--db", self.db, "--apply")
        self.assertEqual(len(self.events()), 2)
        code, out, _ = self.cli("import-bridge", "--db", self.db, "--since", "2030-01-13", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("events=2", out)
        self.assertIn("imported=0", out)
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.cursor(), "2030-01-14T09:00:00Z")
        self.cli("import-bridge", "--db", self.db, "--since", "2030-01-13", "--apply")
        self.assertEqual(self.cursor(), "2030-01-14T09:00:00Z")

    def test_held_auto_import_writes_nothing_and_keeps_the_cursor(self):
        os.environ["MEDLOG_AUTO_MAX_CHANGES"] = "1"
        self.add_event("e1", "Fakepril", "2030-01-13", "08", "2030-01-13T09:00:00Z")
        self.add_event("e2", "Fakepril", "2030-01-14", "08", "2030-01-14T09:00:00Z")
        code, out, _ = self.cli("import-bridge", "--db", self.db, "--auto")
        self.assertEqual(code, 0)
        self.assertIn("held=too_many_changes", out)
        self.assertEqual(self.events(), [])
        self.assertIsNone(self.cursor())
        code, out, _ = self.cli("import-bridge", "--db", self.db, "--since", "2030-01-13", "--apply")  # explicit backfill applies
        self.assertEqual(len(self.events()), 2)

    def test_unmapped_events_do_not_block_the_cursor_and_are_recoverable(self):
        self.add_event("e1", "Unknownium", "2030-01-14", "08", "2030-01-14T09:00:00Z", concept="999")
        _, out, _ = self.cli("import-bridge", "--db", self.db, "--apply")
        self.assertIn("unmapped=1", out)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.cursor(), "2030-01-14T09:00:00Z")
        code, out, _ = self.cli("unmapped", "--days", "30", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("Unknownium", out)
        self.assertIn("concept 999", out)
        self.cli("map-apple", "Unknownium", "fk")
        _, out, _ = self.cli("import-bridge", "--db", self.db, "--since", "2030-01-14", "--apply")
        self.assertIn("imported=1", out)

    def test_concept_key_maps_when_the_name_is_not_mapped(self):
        self.add_event("e1", "Renamed in app", "2030-01-14", "09", "2030-01-14T10:00:00Z", concept="111")
        _, out, _ = self.cli("import-bridge", "--db", self.db, "--apply")
        self.assertIn("imported=1", out)
        self.assertEqual(self.events()[0]["med"], "fk2")

    def test_missing_table_is_not_an_error_and_leaves_no_cursor(self):
        other = str(Path(self.tmp.name) / "old.sqlite")
        old = sqlite3.connect(other)  # an older receiver: a real database that lacks the dose-event table
        old.execute("CREATE TABLE samples (x TEXT)")
        old.commit()
        old.close()
        code, out, _ = self.cli("import-bridge", "--db", other, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("events=0", out)
        self.assertIsNone(self.cursor())

    def test_missing_required_column_and_missing_file_are_errors(self):
        bad = str(Path(self.tmp.name) / "bad.sqlite")
        con = sqlite3.connect(bad)
        con.execute("CREATE TABLE medication_dose_events (client_record_id TEXT)")
        con.commit()
        con.close()
        code, _, err = self.cli("import-bridge", "--db", bad)
        self.assertEqual(code, 2)
        self.assertIn("lacks required column", err)
        code, _, err = self.cli("import-bridge", "--db", str(Path(self.tmp.name) / "nope.sqlite"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_optional_columns_may_be_absent(self):
        old = str(Path(self.tmp.name) / "slim.sqlite")
        con = sqlite3.connect(old)
        con.execute("CREATE TABLE medication_dose_events (client_record_id TEXT, medication_name TEXT, status TEXT, "
                    "start_time TEXT, updated_at TEXT)")
        con.execute("INSERT INTO medication_dose_events VALUES ('s1','Fakepril','taken','2030-01-14T08:00:00Z','2030-01-14T09:00:00Z')")
        con.commit()
        con.close()
        code, out, _ = self.cli("import-bridge", "--db", old, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("imported=1", out)

    def test_bad_since_and_no_leftover_export_file(self):
        self.add_event("e1", "Fakepril", "2030-01-14", "08", "2030-01-14T09:00:00Z")
        self.assertEqual(self.cli("import-bridge", "--db", self.db, "--since", "yesterday")[0], 2)
        self.cli("import-bridge", "--db", self.db, "--apply")
        self.assertEqual(list(self.home.glob("bridge-2*.json")), [])

    def test_uses_the_configured_time_zone(self):
        os.environ["MEDLOG_TZ"] = "Asia/Tokyo"  # 23:30Z on the 14th is 08:30 on the 15th in Tokyo
        os.environ["MEDLOG_NOW"] = "2030-01-15T12:00:00"
        self.add_event("e1", "Fakepril", "2030-01-14", "23", "2030-01-15T00:00:00Z")
        self.cli("import-bridge", "--db", self.db, "--apply")
        ev = self.events()[0]
        self.assertEqual(ev["date"], "2030-01-15")

    def test_since_is_a_local_date_east_of_utc(self):
        os.environ["MEDLOG_TZ"] = "Asia/Tokyo"  # 22:00Z on the 12th is already the 13th in Tokyo
        os.environ["MEDLOG_NOW"] = "2030-01-15T12:00:00"
        self.add_event("e0", "Fakepril", "2030-01-12", "22", "2030-01-12T23:00:00Z")  # 07:00 on the 13th in Tokyo
        self.add_event("e1", "Fakepril", "2030-01-12", "23", "2030-01-12T23:59:00Z")
        code, out, _ = self.cli("import-bridge", "--db", self.db, "--since", "2030-01-13")
        self.assertIn("events=2", out)
        code, out, _ = self.cli("unmapped", "--days", "5", "--db", self.db)
        self.assertEqual(code, 0)

    def test_since_excludes_events_before_the_local_date(self):
        os.environ["MEDLOG_TZ"] = "Pacific/Pago_Pago"  # 03:00Z on the 14th is still the 13th (UTC-11)
        self.add_event("e1", "Fakepril", "2030-01-14", "03", "2030-01-14T04:00:00Z")
        _, out, _ = self.cli("import-bridge", "--db", self.db, "--since", "2030-01-14")
        self.assertIn("events=0", out)


if __name__ == "__main__":
    unittest.main()
