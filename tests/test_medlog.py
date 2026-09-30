"""Tests for medlog. FAKE data only, in temp directories."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import medlog  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"MEDLOG_HOME": self.tmp.name, "MEDLOG_TZ": "Pacific/Auckland",
                    "MEDLOG_NOW": "2030-01-15T09:30:00", "MEDLOG_STORE": "jsonl"}
        self.old = {k: os.environ.get(k) for k in list(self.env) + ["MEDLOG_V1_PATH"]}
        os.environ.update(self.env)
        os.environ.pop("MEDLOG_V1_PATH", None)
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = medlog.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def add_daily(self, mid="fakepril", *times):
        return self.run_cli("add-med", mid, "--name", "Fakepril", "--dose", "50", "--unit", "mg",
                            *sum([["--time", t] for t in (times or ("08:00", "20:00"))], []), "--start", "2030-01-10")


class TestLog(Base):
    def test_unknown_medication_refused(self):
        code, _, err = self.run_cli("log", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown medication", err)

    def test_time_only_when_user_gives_it(self):
        self.add_daily()
        code, out, _ = self.run_cli("--json", "log", "fakepril")
        event = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIsNone(event["time"])
        self.assertEqual(event["time_source"], "unspecified")
        self.assertEqual(event["logged_at"][:16], "2030-01-15T09:30")
        code, out, _ = self.run_cli("--json", "log", "fakepril", "--date", "2030-01-14", "--time", "20:05")
        self.assertEqual(json.loads(out)["time"], "20:05")
        self.assertEqual(json.loads(out)["time_source"], "user")

    def test_slot_from_time_and_auto_slot(self):
        self.add_daily()
        _, out, _ = self.run_cli("--json", "log", "fakepril", "--time", "08:10")
        self.assertEqual(json.loads(out)["slot"], "morning")
        _, out, _ = self.run_cli("--json", "log", "fakepril")
        self.assertEqual(json.loads(out)["slot"], "evening")  # first free slot

    def test_duplicate_refused_and_force(self):
        self.add_daily()
        self.run_cli("log", "fakepril", "--time", "08:00")
        code, _, err = self.run_cli("log", "fakepril", "--time", "08:05")
        self.assertEqual(code, 3)
        self.assertIn("already has", err)
        code, _, _ = self.run_cli("log", "fakepril", "--time", "08:05", "--force")
        self.assertEqual(code, 0)

    def test_future_refused(self):
        self.add_daily()
        code, _, err = self.run_cli("log", "fakepril", "--time", "23:00")
        self.assertEqual(code, 2)
        self.assertIn("future", err)
        code, _, _ = self.run_cli("log", "fakepril", "--date", "2030-01-16")
        self.assertEqual(code, 2)

    def test_bad_time_and_dry_run(self):
        self.add_daily()
        self.assertEqual(self.run_cli("log", "fakepril", "--time", "8am")[0], 2)
        code, out, _ = self.run_cli("log", "fakepril", "--time", "08:00", "--dry-run")
        self.assertIn("DRY RUN", out)
        self.assertEqual(self.run_cli("show")[1].strip(), "no records in that period")

    def test_void_and_skipped_missed_extra(self):
        self.add_daily()
        _, out, _ = self.run_cli("--json", "log", "fakepril", "--time", "08:00")
        eid = json.loads(out)["id"]
        self.assertEqual(self.run_cli("void", eid, "--reason", "logged by mistake")[0], 0)
        self.assertEqual(self.run_cli("void", eid, "--reason", "again")[0], 2)
        self.assertEqual(self.run_cli("log", "fakepril", "--status", "skipped", "--time", "08:00")[0], 0)
        self.assertEqual(self.run_cli("log", "fakepril", "--status", "extra", "--time", "09:00")[0], 0)
        self.assertEqual(self.run_cli("log", "fakepril", "--status", "extra", "--time", "09:10")[0], 0)  # extras may repeat


class TestMissing(Base):
    def test_missing_respects_start_now_and_recorded(self):
        self.add_daily()  # start 2030-01-10, now 2030-01-15 09:30
        self.run_cli("log", "fakepril", "--date", "2030-01-14", "--time", "08:00")
        self.run_cli("log", "fakepril", "--date", "2030-01-14", "--time", "20:00")
        _, out, _ = self.run_cli("--json", "missing", "--days", "30")
        rows = json.loads(out)
        keys = {(r["date"], r["slot"]) for r in rows}
        self.assertNotIn(("2030-01-14", "morning"), keys)
        self.assertNotIn(("2030-01-09", "morning"), keys)          # before start
        self.assertIn(("2030-01-10", "morning"), keys)
        self.assertIn(("2030-01-15", "morning"), keys)              # 08:00 already due at 09:30
        self.assertNotIn(("2030-01-15", "evening"), keys)           # 20:00 not due yet
        self.assertEqual(len([r for r in rows if r["date"] == "2030-01-10"]), 2)

    def test_stop_med_ends_expectations(self):
        self.add_daily()
        self.run_cli("stop-med", "fakepril", "--date", "2030-01-12")
        _, out, _ = self.run_cli("--json", "missing", "--days", "30")
        self.assertEqual({r["date"] for r in json.loads(out)}, {"2030-01-10", "2030-01-11", "2030-01-12"})

    def test_weekly_and_prn(self):
        self.run_cli("add-med", "fakeweekly", "--name", "Fakeweekly", "--kind", "weekly", "--weekday", "fri", "--start", "2030-01-01")
        self.run_cli("add-med", "fakeprn", "--name", "Fakeprn", "--kind", "prn", "--start", "2030-01-01")
        _, out, _ = self.run_cli("--json", "missing", "--days", "30")
        rows = json.loads(out)
        self.assertTrue(all(r["med"] == "fakeweekly" for r in rows))
        self.assertEqual({r["date"] for r in rows}, {"2030-01-04", "2030-01-11"})  # Fridays up to now

    def test_status_and_csv(self):
        self.add_daily()
        self.run_cli("log", "fakepril", "--time", "08:00", "--note", "with food")
        code, out, _ = self.run_cli("status")
        self.assertIn("1 recorded", out)
        code, out, _ = self.run_cli("show", "--format", "csv")
        self.assertTrue(out.startswith("date,slot,med,status"))
        self.assertIn("with food", out)


class TestRegistry(Base):
    def test_add_replace_validation(self):
        self.assertEqual(self.add_daily()[0], 0)
        self.assertEqual(self.add_daily()[0], 2)  # exists
        self.assertEqual(self.run_cli("add-med", "x", "--name", "X")[0], 2)  # daily needs a time
        self.assertEqual(self.run_cli("add-med", "y", "--name", "Y", "--kind", "weekly")[0], 2)
        self.assertEqual(self.run_cli("add-med", "z", "--name", "Z", "--time", "25:00")[0], 2)

    def test_same_label_times_use_hhmm_slots(self):
        self.run_cli("add-med", "fk", "--name", "Fk", "--time", "07:00", "--time", "10:00", "--start", "2030-01-15")
        _, out, _ = self.run_cli("--json", "log", "fk", "--time", "07:05")
        self.assertEqual(json.loads(out)["slot"], "07:00")


if __name__ == "__main__":
    unittest.main()


class TestVersion(unittest.TestCase):
    def test_version_flag_prints_the_package_version(self):
        import io, contextlib
        self.assertRegex(medlog.__version__, r"^\d+\.\d+\.\d+$")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            medlog.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(medlog.__version__, out.getvalue())


class TestFrozenContracts(Base):
    """Consumers (the check-in recipe, the health-insights adherence module) parse these shapes: change them only with care."""

    def test_json_missing_rows_have_exactly_date_med_name_slot(self):
        self.add_daily("fakepril", "08:00")
        code, out, _ = self.run_cli("--json", "missing", "--days", "2")
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), {"date", "med", "name", "slot"})
            self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual({r["med"] for r in rows}, {"fakepril"})

    def test_exit_codes_are_0_2_3(self):
        self.add_daily("fakepril", "08:00")
        self.assertEqual(self.run_cli("log", "fakepril", "--time", "08:05")[0], 0)
        self.assertEqual(self.run_cli("log", "fakepril", "--time", "08:06")[0], 3)  # duplicate slot
        self.assertEqual(self.run_cli("log", "unknown-id")[0], 2)  # validation error
