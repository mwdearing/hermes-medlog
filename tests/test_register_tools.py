"""Tests for `medlog unmapped`, `map-apple` and `check-med`. FAKE ids and names only, in temp directories."""
import contextlib
import io
import json
import os
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
        self.data = Path(self.tmp.name)
        self.map_file = self.data / "apple_med_map.json"
        self.exports = self.data / "bridge-exports"
        self.exports.mkdir()
        self.env = {"MEDLOG_HOME": str(self.data), "MEDLOG_TZ": "Pacific/Auckland", "MEDLOG_NOW": "2030-01-15T12:00:00",
                    "MEDLOG_APPLE_MAP": str(self.map_file), "MEDLOG_STORE": "jsonl"}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
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

    def add_daily(self, mid="fk_daily"):
        self.run_cli("add-med", mid, "--name", "Fake Daily", "--kind", "daily", "--time", "09:00",
                     "--dose", "5", "--unit", "mg", "--start", "2030-01-01")

    def export(self, name, items):
        (self.exports / name).write_text(json.dumps(items))

    @staticmethod
    def ev(ext, med, cid, day, status="taken"):
        return {"external_id": ext, "medication": med, "medication_id": cid, "status": status,
                "start": f"{day}T09:00:00+13:00"}


class TestUnmapped(Base):
    def test_lists_unmapped_names_with_counts_and_newest_date(self):
        self.add_daily()
        self.map_file.write_text(json.dumps({"Fake Mapped": "fk_daily"}))
        self.export("bridge-meds-20300115_100000.json", [
            self.ev("a", "Fake Mapped", "c-1", "2030-01-14"),
            self.ev("b", "Fake Other", "c-2", "2030-01-10"),
            self.ev("c", "Fake Other", "c-2", "2030-01-13"),
            self.ev("d", "Fake Other", "c-2", "2030-01-13", status="ignored"),
        ])
        code, out, _ = self.run_cli("unmapped", "--days", "14")
        self.assertEqual(code, 0)
        self.assertIn("Fake Other", out)
        self.assertIn("concept c-2", out)
        self.assertIn("2030-01-13", out)
        self.assertNotIn("Fake Mapped", out)

    def test_concept_key_counts_as_mapped(self):
        self.add_daily()
        self.map_file.write_text(json.dumps({"concept c-9": "fk_daily"}))
        self.export("bridge-meds-20300115_100000.json", [self.ev("a", "Some Name", "c-9", "2030-01-14")])
        code, out, _ = self.run_cli("--json", "unmapped")
        self.assertEqual(json.loads(out), [])

    def test_map_pointing_at_missing_registry_id_is_unmapped(self):
        self.map_file.write_text(json.dumps({"Fake Ghost": "not_registered"}))
        self.export("bridge-meds-20300115_100000.json", [self.ev("a", "Fake Ghost", "c-3", "2030-01-14")])
        _, out, _ = self.run_cli("--json", "unmapped")
        self.assertEqual(json.loads(out)[0]["events"], 1)

    def test_days_window_excludes_old_events(self):
        self.export("bridge-meds-20300115_100000.json", [
            self.ev("a", "Fake Old", "c-4", "2029-12-01"), self.ev("b", "Fake New", "c-5", "2030-01-14")])
        _, out, _ = self.run_cli("--json", "unmapped", "--days", "14")
        self.assertEqual([r["name"] for r in json.loads(out)], ["Fake New"])

    def test_read_only_and_no_exports_is_clean(self):
        code, out, _ = self.run_cli("unmapped")
        self.assertEqual(code, 0)
        self.assertIn("nothing unmapped", out)
        self.assertFalse(self.map_file.exists())


class TestMapApple(Base):
    def test_adds_key(self):
        self.add_daily()
        code, _, _ = self.run_cli("map-apple", "concept c-1", "fk_daily")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.map_file.read_text()), {"concept c-1": "fk_daily"})

    def test_unknown_id_refused_and_nothing_written(self):
        code, _, err = self.run_cli("map-apple", "Fake Name", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)
        self.assertFalse(self.map_file.exists())

    def test_existing_key_refused_without_replace_case_insensitive(self):
        self.add_daily()
        self.run_cli("add-med", "fk_two", "--name", "Fake Two", "--kind", "prn")
        self.run_cli("map-apple", "Fake Name", "fk_daily")
        code, _, err = self.run_cli("map-apple", "FAKE NAME", "fk_two")
        self.assertEqual(code, 2)
        self.assertIn("--replace", err)
        self.assertEqual(json.loads(self.map_file.read_text()), {"Fake Name": "fk_daily"})
        code, _, _ = self.run_cli("map-apple", "FAKE NAME", "fk_two", "--replace")
        self.assertEqual(code, 0)
        self.assertEqual(list(json.loads(self.map_file.read_text()).values()), ["fk_two"])

    def test_backup_written_before_change(self):
        self.add_daily()
        self.map_file.write_text(json.dumps({"Fake Old": "fk_daily"}))
        self.run_cli("map-apple", "Fake New", "fk_daily")
        backups = list(self.data.glob("apple_med_map.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), {"Fake Old": "fk_daily"})
        self.assertEqual(json.loads(self.map_file.read_text()), {"Fake Old": "fk_daily", "Fake New": "fk_daily"})

    def test_corrupt_map_is_not_overwritten(self):
        self.add_daily()
        self.map_file.write_text("{not json")
        code, _, _ = self.run_cli("map-apple", "Fake New", "fk_daily")
        self.assertEqual(code, 2)
        self.assertEqual(self.map_file.read_text(), "{not json")

    def test_mapped_key_is_used_by_import(self):
        self.add_daily()
        self.run_cli("map-apple", "concept c-7", "fk_daily")
        f = self.data / "in.json"
        f.write_text(json.dumps([self.ev("z1", "Whatever", "c-7", "2030-01-14")]))
        _, out, _ = self.run_cli("import-apple", str(f))
        self.assertIn("unmapped=0", out)


class TestCheckMed(Base):
    def test_complete(self):
        self.add_daily()
        self.run_cli("map-apple", "concept c-1", "fk_daily")
        self.run_cli("log", "fk_daily", "--time", "09:00", "--date", "2030-01-14", "--source", "apple_health")
        code, out, _ = self.run_cli("check-med", "fk_daily")
        self.assertEqual(code, 0)
        self.assertIn("complete", out)

    def test_unknown_id(self):
        code, out, err = self.run_cli("check-med", "nope")
        self.assertEqual(code, 2)
        self.assertIn("not in the registry", out + err)

    def test_no_map_key_and_no_import_are_reported(self):
        self.add_daily()
        code, out, _ = self.run_cli("check-med", "fk_daily")
        self.assertEqual(code, 2)
        self.assertIn("no Apple map key", out)
        self.assertIn("no Apple import", out)

    def test_prn_is_not_covered_by_missing_but_that_is_expected(self):
        self.run_cli("add-med", "fk_prn", "--name", "Fake Prn", "--kind", "prn")
        self.run_cli("map-apple", "Fake Prn", "fk_prn")
        self.run_cli("log", "fk_prn", "--status", "extra", "--time", "10:00", "--source", "apple_health")
        code, out, _ = self.run_cli("check-med", "fk_prn")
        self.assertEqual(code, 0)
        self.assertIn("as needed", out)

    def test_read_only(self):
        self.add_daily()
        before = (self.data / "medications.json").read_text()
        self.run_cli("check-med", "fk_daily")
        self.assertEqual((self.data / "medications.json").read_text(), before)
        self.assertFalse(self.map_file.exists())

    def test_json(self):
        self.add_daily()
        code, out, _ = self.run_cli("--json", "check-med", "fk_daily")
        self.assertEqual(code, 2)
        data = json.loads(out)
        self.assertFalse(data["complete"])
        self.assertTrue(data["problems"])


if __name__ == "__main__":
    unittest.main()
