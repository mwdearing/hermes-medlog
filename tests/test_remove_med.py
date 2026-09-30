"""Tests for medlog remove-med. FAKE data only, in temp directories."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import medlog  # noqa: E402


class RemoveMedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.env = {"MEDLOG_HOME": str(self.data), "MEDLOG_TZ": "Pacific/Auckland",
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
        import contextlib, io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = medlog.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def reg_path(self):
        return Path(self.tmp.name) / "data" / "medications.json"

    def backup_count(self):
        return len(list(Path(self.tmp.name).glob("data/medications.json.bak-*")))

    def test_remove_unused_med(self):
        self.run_cli("add-med", "fk", "--name", "Fk", "--time", "08:00", "--start", "2030-01-10")
        code, out, _ = self.run_cli("remove-med", "fk")
        self.assertEqual(code, 0)
        self.assertIn("fk", out)
        # gone from the registry
        reg = json.loads(self.reg_path().read_text())
        self.assertNotIn("fk", reg["medications"])

    def test_refuses_when_events_reference_id(self):
        self.run_cli("add-med", "fk", "--name", "Fk", "--time", "08:00", "--start", "2030-01-10")
        self.run_cli("log", "fk", "--time", "08:00")
        code, _, err = self.run_cli("remove-med", "fk")
        self.assertEqual(code, 2)
        self.assertIn("still referenced", err)
        # still present
        reg = json.loads(self.reg_path().read_text())
        self.assertIn("fk", reg["medications"])

    def test_refuses_unknown_med(self):
        code, _, err = self.run_cli("remove-med", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown", err)

    def test_makes_timestamped_backup(self):
        self.run_cli("add-med", "fk", "--name", "Fk", "--time", "08:00", "--start", "2030-01-10")
        self.run_cli("remove-med", "fk")
        self.assertGreaterEqual(self.backup_count(), 1)


if __name__ == "__main__":
    unittest.main()
