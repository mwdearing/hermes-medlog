"""Public cron recipes (plain text). FAKE data only."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import medlog
from medlog.recipes import checkin, weekly_report

ROOT = Path(__file__).resolve().parent.parent
ENV = ("MEDLOG_CONFIG", "MEDLOG_HOME", "MEDLOG_TZ", "MEDLOG_NOW", "MEDLOG_STORE")


class RecipeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = {k: os.environ.get(k) for k in ENV}
        os.environ.update(MEDLOG_CONFIG=str(Path(self.tmp.name) / "none.json"), MEDLOG_HOME=self.tmp.name,
                          MEDLOG_TZ="Pacific/Auckland", MEDLOG_NOW="2030-01-15T21:30:00", MEDLOG_STORE="jsonl")
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def cli(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return medlog.main(list(argv))

    def test_checkin_is_silent_when_nothing_is_registered_or_missing(self):
        self.assertEqual(checkin.build_message(), "")
        self.cli("add-med", "fk", "--name", "Fakepril", "--time", "08:00", "--start", "2030-01-15")
        self.cli("log", "fk", "--time", "08:05")
        self.assertEqual(checkin.build_message(), "")

    def test_checkin_lists_missing_slots_plainly(self):
        self.cli("add-med", "fk", "--name", "Fakepril", "--time", "08:00", "--time", "20:00", "--start", "2030-01-10")
        self.cli("log", "fk", "--date", "2030-01-14", "--time", "08:00")
        msg = checkin.build_message()
        self.assertIn("Not logged:", msg)
        self.assertIn("- Fakepril:", msg)
        self.assertIn("Jan 14 evening", msg)
        self.assertNotIn("**", msg)
        self.assertNotIn("Jan 11", msg)

    def test_checkin_caps_at_eight_slots(self):
        self.cli("add-med", "fk", "--name", "Fakepril", "--time", "08:00", "--time", "20:00", "--start", "2030-01-01")
        msg = checkin.build_message(days=10)
        self.assertIn("older not listed", msg)
        self.assertEqual(sum(part.count(";") + 1 for part in msg.splitlines() if part.startswith("- Fakepril")), 8)

    def test_checkin_mentions_a_held_import_from_counts_only_log(self):
        (Path(self.tmp.name) / "auto-import.log").write_text(
            "2030-01-15T20:00:00+13:00 file=x.json imported=0 corrected=0 duplicates=0 mismatches=1 result=held:dose_mismatch\n")
        msg = checkin.build_message()
        self.assertIn("dose_mismatch", msg)

    def test_weekly_report_counts_imports_and_gaps(self):
        (Path(self.tmp.name) / "auto-import.log").write_text(
            "2030-01-14T20:00:00+13:00 file=x.json imported=2 corrected=1 duplicates=0 mismatches=0 result=applied\n"
            "2029-12-01T20:00:00+13:00 file=y.json imported=9 corrected=9 duplicates=0 mismatches=0 result=applied\n")
        self.cli("add-med", "fk", "--name", "Fakepril", "--time", "08:00", "--start", "2030-01-13")
        text = weekly_report.build_report()
        self.assertIn("Automatic imports: 1", text)
        self.assertIn("Doses imported: 2", text)
        self.assertIn("Time corrections from Health: 1", text)
        self.assertIn("- Fakepril x", text)

    def test_modules_run_as_scripts_and_never_write(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        before = sorted(p.name for p in Path(self.tmp.name).iterdir())
        for mod in ("medlog.recipes.checkin", "medlog.recipes.weekly_report"):
            done = subprocess.run([sys.executable, "-m", mod], env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), before)


if __name__ == "__main__":
    unittest.main()
