"""medlog doctor and the bridge_db setting. FAKE data only."""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import medlog

ENV = ("MEDLOG_CONFIG", "MEDLOG_HOME", "MEDLOG_TZ", "MEDLOG_NOW", "MEDLOG_STORE", "MEDLOG_BRIDGE_DB")
COLS = ("client_record_id TEXT, medication_name TEXT, medication_concept_key TEXT, status TEXT, status_raw TEXT, "
        "start_time TEXT, scheduled_time TEXT, dose REAL, unit TEXT, updated_at TEXT")


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = {k: os.environ.get(k) for k in ENV}
        self.addCleanup(self.restore)
        base = Path(self.tmp.name)
        self.home = base / "data"
        self.home.mkdir()
        self.cfg = base / "config.json"
        os.environ.update(MEDLOG_CONFIG=str(self.cfg), MEDLOG_HOME=str(self.home), MEDLOG_TZ="UTC",
                          MEDLOG_NOW="2030-01-15T12:00:00", MEDLOG_STORE="jsonl")
        os.environ.pop("MEDLOG_BRIDGE_DB", None)

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = medlog.cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def make_db(self, path, rows=()):
        con = sqlite3.connect(path)
        con.execute(f"CREATE TABLE medication_dose_events ({COLS})")
        for r in rows:
            con.execute("INSERT INTO medication_dose_events VALUES (?,?,?,?,?,?,?,?,?,?)", r)
        con.commit()
        con.close()
        ts = 1894708800  # 2030-01-15 12:00 UTC
        os.utime(path, (ts, ts))

    def set_cfg(self, **kw):
        self.cfg.write_text(json.dumps(kw))

    def test_ok(self):
        db = self.home / "r.sqlite"
        self.make_db(db)
        self.set_cfg(bridge_db=str(db))
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("status: ok", out)

    def test_unset_is_a_warning_not_an_error(self):
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("WARNING: bridge_db is not set", out)

    def test_missing_is_error(self):
        self.set_cfg(bridge_db=str(self.home / "nope.sqlite"))
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 2)
        self.assertIn("not found", out)

    def test_zero_byte_is_error_and_untouched(self):
        db = self.home / "zero.sqlite"
        db.write_bytes(b"")
        self.set_cfg(bridge_db=str(db))
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 2)
        self.assertIn("0-byte", out)
        self.assertEqual(db.stat().st_size, 0)

    def test_stale_is_warning(self):
        db = self.home / "old.sqlite"
        self.make_db(db)
        os.utime(db, (1894708800 - 10 * 86400,) * 2)
        self.set_cfg(bridge_db=str(db))
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("WARNING", out)

    def test_unmapped_counted_without_names(self):
        db = self.home / "u.sqlite"
        self.make_db(db, [("a1", "Fakepril", "111", "taken", "taken", "2030-01-14T08:00:00Z", None, 10, "mg", "2030-01-14T08:00:00Z")])
        self.set_cfg(bridge_db=str(db))
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("unmapped names (14 days): 1", out)
        self.assertNotIn("Fakepril", out)

    def test_json_and_path_with_special_chars(self):
        d = self.home / "we?ird #dir"
        d.mkdir()
        db = d / "r.sqlite"
        self.make_db(db)
        self.set_cfg(bridge_db=str(db))
        code, out, _ = self.cli("--json", "doctor")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(data["bridge_db"]["opens_read_only"])
        self.assertEqual(data["status"], "ok")

    def test_unmapped_and_import_bridge_use_the_config_without_db(self):
        db = self.home / "c.sqlite"
        self.make_db(db, [("a1", "Fakepril", "111", "taken", "taken", "2030-01-14T08:00:00Z", None, 10, "mg", "2030-01-14T08:00:00Z")])
        self.set_cfg(bridge_db=str(db))
        code, out, _ = self.cli("unmapped")
        self.assertEqual(code, 0)
        self.assertIn("Fakepril", out)
        code, out, _ = self.cli("import-bridge")
        self.assertIn("import-bridge: events=1", out)

    def test_import_bridge_without_db_or_config_errors(self):
        code, _, err = self.cli("import-bridge")
        self.assertEqual(code, 2)
        self.assertIn("bridge_db", err)

    def test_zero_byte_db_is_an_error_for_import_bridge(self):
        db = self.home / "z.sqlite"
        db.write_bytes(b"")
        code, _, err = self.cli("import-bridge", "--db", str(db))
        self.assertEqual(code, 2)
        self.assertIn("0-byte", err)

    def test_db_override_wins(self):
        good = self.home / "g.sqlite"
        self.make_db(good)
        self.set_cfg(bridge_db=str(self.home / "nope.sqlite"))
        code, out, _ = self.cli("import-bridge", "--db", str(good))
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
