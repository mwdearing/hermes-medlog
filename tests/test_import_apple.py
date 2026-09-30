"""Tests for `medlog import-apple`. FAKE data only, in temp directories."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import medlog
from medlog.apple import (build_event, classify_status, duplicate_check,
                          load_map, local_dt, name_to_id)
from medlog import pick_slot


class ImportAppleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"MEDLOG_HOME": self.tmp.name, "MEDLOG_TZ": "Pacific/Auckland",
                    "MEDLOG_NOW": "2030-01-15T12:00:00", "MEDLOG_APPLE_MAP":
                    str(Path(self.tmp.name) / "apple_med_map.json")}
        self.old = {k: os.environ.get(k) for k in list(self.env)}
        os.environ.update(self.env)
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def map(self, mapping):
        Path(self.env["MEDLOG_APPLE_MAP"]).write_text(json.dumps(mapping))

    def add_daily(self, mid="fk_apple", name="Fake Apple"):
        medlog.main(["add-med", mid, "--name", name, "--kind", "daily",
                     "--time", "09:00", "--dose", "50", "--unit", "mg",
                     "--start", "2030-01-01"])

    def run_import(self, data, apply=False):
        f = Path(self.tmp.name) / "apple.json"
        f.write_text(json.dumps(data))
        argv = ["import-apple", str(f)]
        if apply:
            argv.append("--apply")
        out, err = self._run(argv)
        return out

    def _run(self, argv):
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = medlog.main(argv)
        return out.getvalue(), code


class TestMapAndStatus(ImportAppleBase):
    def test_name_maps_to_registry_id(self):
        self.map({"Fakemab": "fkw", "Fakezide": "fkz"})
        self.assertEqual(name_to_id("fakemab", load_map()), "fkw")
        self.assertEqual(name_to_id("FAKEMAB", load_map()), "fkw")
        self.assertEqual(name_to_id("Fakemab ", load_map()), "fkw")

    def test_unmapped_name_not_imported(self):
        self.map({"Fakemab": "fkw"})
        out = self.run_import([{"external_id": "x1", "medication": "Mystery Med",
                                "status": "taken", "start": "2030-01-10T09:00:00+13:00"}])
        self.assertIn("unmapped=1", out)
        self.assertIn("imported=0", out)

    def test_id_fallback_maps_when_name_is_not_in_the_map(self):
        self.add_daily()
        self.map({"concept fake-id-1": "fk_apple"})
        out = self.run_import([{"external_id": "i1", "medication": "Some Display Name",
                                "medication_id": "fake-id-1", "status": "taken",
                                "start": "2030-01-10T09:00:00+13:00"}])
        self.assertIn("imported=1", out)
        self.assertIn("unmapped=0", out)

    def test_name_match_wins_over_id_fallback(self):
        self.add_daily()
        self.add_daily("fk_other", "Fake Other")
        self.map({"Named Med": "fk_apple", "concept fake-id-2": "fk_other"})
        self.run_import([{"external_id": "i2", "medication": "Named Med",
                          "medication_id": "fake-id-2", "status": "taken",
                          "start": "2030-01-10T09:00:00+13:00"}], apply=True)
        applied = [e for e in medlog.store_for("fk_apple").events() if e.get("apple_id") == "i2"]
        self.assertEqual([e["med"] for e in applied], ["fk_apple"])

    def test_id_not_in_map_stays_unmapped(self):
        self.add_daily()
        self.map({"concept fake-id-1": "fk_apple"})
        out = self.run_import([{"external_id": "i3", "medication": "Some Display Name",
                                "medication_id": "fake-id-9", "status": "taken",
                                "start": "2030-01-10T09:00:00+13:00"}])
        self.assertIn("unmapped=1", out)
        self.assertIn("imported=0", out)

    def test_status_other_than_taken_or_skipped_is_ignored(self):
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()
        out = self.run_import([{"external_id": "x1", "medication": "Fake Apple",
                                "status": "snoozed", "start": "2030-01-10T09:00:00+13:00"}])
        self.assertIn("ignored=1", out)
        self.assertIn("imported=0", out)
        self.assertEqual(classify_status("taken"), "taken")
        self.assertEqual(classify_status("skipped"), "skipped")
        self.assertEqual(classify_status("snoozed"), "ignored")
        self.assertEqual(classify_status(""), "ignored")


class TestLocalDt(ImportAppleBase):
    def test_parse_with_utc_offset_into_local_time(self):
        dt = local_dt("2030-01-10T09:05:00+13:00")
        self.assertEqual(dt.strftime("%H:%M"), "09:05")
        self.assertEqual(dt.date().isoformat(), "2030-01-10")


class TestDuplicateAndMismatch(ImportAppleBase):
    def test_external_id_repeat_is_duplicate(self):
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()
        data = [{"external_id": "a1", "medication": "Fake Apple", "status": "taken",
                 "start": "2030-01-10T09:05:00+13:00", "dose": 50, "unit": "mg"}]
        existing = []
        new_dt = local_dt("2030-01-10T09:05:00+13:00")
        is_dup, is_mismatch = duplicate_check(data[0], existing, new_dt, "fk_apple")
        self.assertFalse(is_dup)
        applied = [build_event(data[0], new_dt, "fk_apple")]
        is_dup, is_mismatch = duplicate_check(data[0], applied, new_dt, "fk_apple")
        self.assertTrue(is_dup)
        self.assertFalse(is_mismatch)

    def test_same_day_within_3_hours_is_duplicate(self):
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()
        existing = [{"med": "fk_apple", "voided": False, "date": "2030-01-13",
                     "time": "09:00", "dose": 50}]
        new_dt = local_dt("2030-01-13T09:10:00+13:00")
        event = {"external_id": "a6", "dose": 60, "unit": "mg"}
        is_dup, is_mismatch = duplicate_check(event, existing, new_dt, "fk_apple")
        self.assertTrue(is_dup)
        self.assertTrue(is_mismatch)  # dose differs by > 0.01

    def test_same_day_outside_3_hours_imports(self):
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()
        existing = [{"med": "fk_apple", "voided": False, "date": "2030-01-13",
                     "time": "09:00", "dose": 50}]
        new_dt = local_dt("2030-01-13T15:00:00+13:00")
        event = {"external_id": "a9", "dose": 50, "unit": "mg"}
        is_dup, _ = duplicate_check(event, existing, new_dt, "fk_apple")
        self.assertFalse(is_dup)

    def test_weekly_same_local_date_is_duplicate(self):
        medlog.main(["add-med", "fkw", "--name", "Fakemab", "--kind", "weekly",
                     "--weekday", "wed", "--start", "2030-01-01"])
        existing = [{"med": "fkw", "voided": False, "date": "2030-01-15",
                     "time": "09:00", "dose": 1.0}]  # 2030-01-15 is a Wednesday
        new_dt = local_dt("2030-01-15T22:00:00+13:00")
        event = {"external_id": "r1", "dose": 2.0, "unit": "mcg"}
        is_dup, is_mismatch = duplicate_check(event, existing, new_dt, "fkw")
        self.assertTrue(is_dup)
        self.assertTrue(is_mismatch)

    def test_second_apply_is_idempotent(self):
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()
        data = [{"external_id": "a1", "medication": "Fake Apple", "status": "taken",
                 "start": "2030-01-10T09:05:00+13:00", "dose": 50, "unit": "mg"}]
        self.run_import(data, apply=True)
        out2 = self.run_import(data, apply=True)
        self.assertIn("imported=0", out2)


class TestWeeklySlot(ImportAppleBase):
    def test_weekly_import_uses_pick_slot_not_clock_hour(self):
        # Bug 1: a weekly med's slot is "" in medlog; build_event derived the slot
        # from the clock hour, so `medlog missing` still lists the week as MISSING.
        self.map({"Fake Weekly": "fk_weekly"})
        medlog.main(["add-med", "fk_weekly", "--name", "Fake Weekly", "--kind", "weekly",
                     "--weekday", "wed", "--start", "2030-01-01"])
        # 2030-01-16 is a Wednesday. 09:30 local hour would map to "morning" by clock,
        # but the weekly slot is "" (the week).
        event = {"external_id": "w1", "medication": "Fake Weekly", "status": "taken",
                 "start": "2030-01-16T09:30:00+13:00", "dose": 2, "unit": "mg"}
        new_dt = local_dt("2030-01-16T09:30:00+13:00")
        med = medlog.load_registry()["medications"]["fk_weekly"]
        got = build_event(event, new_dt, "fk_weekly")["slot"]
        self.assertEqual(got, pick_slot(med, [], "fk_weekly", new_dt.date().isoformat(), None))


class TestUntimedDuplicate(ImportAppleBase):
    def test_apple_dose_in_slot_with_existing_untimed_record_is_duplicate(self):
        # Bug 2: duplicate_check only compared events that have a time. An Apple
        # dose whose slot on that date already holds an active record (timed or not)
        # is a duplicate.
        self.map({"Fake Daily": "fk_daily"})
        medlog.main(["add-med", "fk_daily", "--name", "Fake Daily", "--kind", "daily",
                     "--time", "09:00", "--dose", "200", "--unit", "mcg", "--start", "2030-01-10"])
        # A record told to the bot WITHOUT a time: slot "morning", no "time" field.
        existing = [{"med": "fk_daily", "voided": False, "date": "2030-01-16",
                     "slot": "morning", "status": "taken"}]
        new_dt = local_dt("2030-01-16T09:32:00+13:00")
        event = {"external_id": "d1", "dose": 200, "unit": "mcg"}
        is_dup, is_mismatch = duplicate_check(event, existing, new_dt, "fk_daily")
        self.assertTrue(is_dup)
        self.assertFalse(is_mismatch)


class TestNoInventedDose(ImportAppleBase):
    def test_apple_event_without_dose_stored_without_dose(self):
        # Bug 3: build_event fell back to the registered default dose when the Apple
        # item had none. It must store no dose (the item said none).
        self.map({"Fake Daily": "fk_daily"})
        medlog.main(["add-med", "fk_daily", "--name", "Fake Daily", "--kind", "daily",
                     "--time", "09:00", "--dose", "200", "--unit", "mcg", "--start", "2030-01-10"])
        event = {"external_id": "d2", "medication": "Fake Daily", "status": "taken",
                 "start": "2030-01-16T21:00:00+13:00", "dose": None, "unit": None}
        new_dt = local_dt("2030-01-16T21:00:00+13:00")
        got = build_event(event, new_dt, "fk_daily")
        self.assertIsNone(got["dose"])
        self.assertIsNone(got["unit"])


class TestAppleCountUnit(ImportAppleBase):
    """Apple Health's Medications app records a logged dose as dose 1 with unit "count" (one dose, amount
    not stated; seen on a real export). It is not an amount: the importer keeps time and status,
    stores no dose, and never compares it with the logged amount (the user's decision)."""

    def setUp(self):
        super().setUp()
        self.map({"Fake Daily": "fk_daily"})
        medlog.main(["add-med", "fk_daily", "--name", "Fake Daily", "--kind", "daily",
                     "--time", "09:00", "--dose", "200", "--unit", "mcg", "--start", "2030-01-10"])

    def test_count_dose_takes_the_configured_current_dose(self):
        # an Apple "count" dose is the medication's configured current dose.
        event = {"external_id": "c1", "medication": "Fake Daily", "status": "taken",
                 "start": "2030-01-16T21:00:00+13:00", "dose": 1, "unit": "count"}
        got = build_event(event, local_dt(event["start"]), "fk_daily")
        self.assertEqual(got["dose"], 200)
        self.assertEqual(got["unit"], "mcg")
        self.assertEqual(got["status"], "taken")
        self.assertEqual(got["time"], "21:00")

    def test_count_dose_without_configured_dose_stays_unknown(self):
        medlog.main(["add-med", "fk_nodose", "--name", "Fake NoDose", "--kind", "weekly",
                     "--weekday", "wed", "--start", "2030-01-01"])
        event = {"external_id": "c5", "medication": "Fake NoDose", "status": "taken",
                 "start": "2030-01-16T09:00:00+13:00", "dose": 1, "unit": "count"}
        got = build_event(event, local_dt(event["start"]), "fk_nodose")
        self.assertIsNone(got["dose"])
        self.assertIsNone(got["unit"])

    def test_count_dose_is_not_a_mismatch(self):
        existing = [{"med": "fk_daily", "voided": False, "date": "2030-01-16",
                     "time": "09:00", "dose": 200, "unit": "mcg"}]
        event = {"external_id": "c2", "dose": 1, "unit": "count"}
        is_dup, is_mismatch = duplicate_check(event, existing, local_dt("2030-01-16T09:20:00+13:00"), "fk_daily")
        self.assertTrue(is_dup)
        self.assertFalse(is_mismatch)

    def test_count_unit_match_is_case_insensitive(self):
        event = {"external_id": "c3", "medication": "Fake Daily", "status": "taken",
                 "start": "2030-01-16T21:00:00+13:00", "dose": 1, "unit": "Count"}
        self.assertEqual(build_event(event, local_dt(event["start"]), "fk_daily")["dose"], 200)

    def test_real_amount_is_still_compared(self):
        existing = [{"med": "fk_daily", "voided": False, "date": "2030-01-16",
                     "time": "09:00", "dose": 200, "unit": "mcg"}]
        event = {"external_id": "c4", "dose": 150, "unit": "mcg"}
        _, is_mismatch = duplicate_check(event, existing, local_dt("2030-01-16T09:20:00+13:00"), "fk_daily")
        self.assertTrue(is_mismatch)


class TestLoggedAt(ImportAppleBase):
    def test_logged_at_is_system_clock_not_apple_time(self):
        # Bug 4: logged_at must be the system clock (now()); the event's own moment
        # goes in date and time.
        event = {"external_id": "a1", "medication": "Fake Apple", "status": "taken",
                 "start": "2030-01-10T09:05:00+13:00", "dose": 50, "unit": "mg"}
        new_dt = local_dt("2030-01-10T09:05:00+13:00")
        got = build_event(event, new_dt, "fk_apple")
        self.assertEqual(got["logged_at"], medlog.now().isoformat())
        self.assertEqual(got["date"], new_dt.date().isoformat())
        self.assertEqual(got["time"], new_dt.strftime("%H:%M"))


if __name__ == "__main__":
    unittest.main()


class LocalDtOffsetValidation(unittest.TestCase):
    """local_dt() must require an explicit UTC offset (review finding 6, 2026-09-22): the importer contract
    is offset-bearing Apple Health timestamps, but a naive timestamp was silently assigned the local
    timezone instead of being rejected."""

    def setUp(self):
        old = os.environ.get("MEDLOG_TZ")
        os.environ["MEDLOG_TZ"] = "Pacific/Auckland"
        self.addCleanup(lambda: os.environ.pop("MEDLOG_TZ", None) if old is None else os.environ.__setitem__("MEDLOG_TZ", old))

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(medlog.MedlogError):
            local_dt("2030-01-10T09:05:00")  # no offset

    def test_offset_bearing_timestamp_still_converts_correctly(self):
        dt = local_dt("2030-01-10T09:05:00+13:00")
        self.assertEqual(dt.isoformat(), "2030-01-10T09:05:00+13:00")

    def test_utc_z_offset_converts_to_local(self):
        dt = local_dt("2030-01-09T20:05:00Z")
        self.assertEqual(dt.isoformat(), "2030-01-10T09:05:00+13:00")


class TestHealthTimeWins(ImportAppleBase):
    """The user's rule: the Apple Health time is the most accurate dose time, so a matching record
    told to the bot with a different (or no) time is corrected to Apple's time, never duplicated."""

    def setUp(self):
        super().setUp()
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()  # one daily slot at 09:00

    def log(self, date, time=None, slot=None, status="taken", dose=None, med="fk_apple"):
        argv = ["log", med, "--date", date, "--status", status]
        if time:
            argv += ["--time", time]
        if slot:
            argv += ["--slot", slot]
        if dose is not None:
            argv += ["--dose", str(dose)]
        medlog.main(argv)

    def apple(self, start, ext="a1", status="taken", dose=50, unit="mg"):
        return {"external_id": ext, "medication": "Fake Apple", "status": status, "start": start,
                "dose": dose, "unit": unit}

    def events(self, med="fk_apple"):
        return medlog.store_for(med).events()

    def test_different_time_is_a_correction_not_a_duplicate(self):
        self.log("2030-01-10", "09:40")
        out = self.run_import([self.apple("2030-01-10T09:12:00+13:00")])
        self.assertIn("imported=0", out)
        self.assertIn("duplicates=0", out)
        self.assertIn("corrected=1", out)

    def test_dry_run_changes_nothing(self):
        self.log("2030-01-10", "09:40")
        before = self.events()
        self.run_import([self.apple("2030-01-10T09:12:00+13:00")])
        self.assertEqual(self.events(), before)

    def test_apply_voids_old_record_and_writes_apple_time(self):
        self.log("2030-01-10", "09:40")
        old_id = self.events()[0]["id"]
        self.run_import([self.apple("2030-01-10T09:12:00+13:00")], apply=True)
        active = [e for e in self.events() if not e["voided"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["time"], "09:12")
        self.assertEqual(active[0]["source"], "apple_health")
        self.assertEqual(active[0]["date"], "2030-01-10")
        self.assertTrue(next(e for e in self.events() if e["id"] == old_id)["voided"])

    def test_untimed_record_gets_apple_time(self):
        self.log("2030-01-10", slot="morning")
        out = self.run_import([self.apple("2030-01-10T09:12:00+13:00")], apply=True)
        self.assertIn("corrected=1", out)
        active = [e for e in self.events() if not e["voided"]]
        self.assertEqual([e["time"] for e in active], ["09:12"])

    def test_same_time_is_a_plain_duplicate(self):
        self.log("2030-01-10", "09:12")
        out = self.run_import([self.apple("2030-01-10T09:12:00+13:00")])
        self.assertIn("duplicates=1", out)
        self.assertIn("corrected=0", out)

    def test_dose_mismatch_is_reported_not_corrected(self):
        self.log("2030-01-10", "09:40", dose=30)
        out = self.run_import([self.apple("2030-01-10T09:12:00+13:00", dose=50)], apply=True)
        self.assertIn("corrected=0", out)
        self.assertIn("mismatches=1", out)
        self.assertEqual([e["time"] for e in self.events() if not e["voided"]], ["09:40"])

    def test_different_status_is_not_corrected(self):
        self.log("2030-01-10", "09:40", status="skipped")
        out = self.run_import([self.apple("2030-01-10T09:12:00+13:00", status="taken")], apply=True)
        self.assertIn("corrected=0", out)
        self.assertEqual([e["status"] for e in self.events() if not e["voided"]], ["skipped"])

    def test_second_apply_is_idempotent_after_a_correction(self):
        self.log("2030-01-10", "09:40")
        data = [self.apple("2030-01-10T09:12:00+13:00")]
        self.run_import(data, apply=True)
        out = self.run_import(data, apply=True)
        self.assertIn("corrected=0", out)
        self.assertIn("imported=0", out)
        self.assertEqual(len([e for e in self.events() if not e["voided"]]), 1)

    def test_weekly_medication_time_is_corrected_on_the_same_date(self):
        medlog.main(["add-med", "wk", "--name", "Fake Weekly", "--kind", "weekly", "--weekday", "wed",
                     "--start", "2030-01-01", "--dose", "1", "--unit", "mL"])
        self.map({"Fake Apple": "fk_apple", "Fake Weekly": "wk"})
        self.log("2030-01-09", "10:00", med="wk", dose=1)
        item = {"external_id": "w1", "medication": "Fake Weekly", "status": "taken",
                "start": "2030-01-09T12:33:00+13:00", "dose": 1, "unit": "mL"}
        out = self.run_import([item], apply=True)
        self.assertIn("corrected=1", out)
        self.assertEqual([e["time"] for e in self.events("wk") if not e["voided"]], ["12:33"])

    def test_correction_keeps_the_recorded_dose_when_apple_states_none(self):
        medlog.main(["add-med", "wk", "--name", "Fake Weekly", "--kind", "weekly", "--weekday", "wed",
                     "--start", "2030-01-01"])  # no configured dose
        self.map({"Fake Weekly": "wk"})
        self.log("2030-01-09", "10:00", med="wk", dose=2)
        item = {"external_id": "w2", "medication": "Fake Weekly", "status": "taken",
                "start": "2030-01-09T12:33:00+13:00", "dose": 1, "unit": "count"}
        self.run_import([item], apply=True)
        (e,) = [e for e in self.events("wk") if not e["voided"]]
        self.assertEqual((e["time"], e["dose"]), ("12:33", 2.0))

    def test_output_keeps_the_original_keys_first(self):
        out = self.run_import([])
        self.assertTrue(out.startswith("import-apple: imported=0 duplicates=0 ignored=0 unmapped=0 mismatches=0"))


class TestAfterMidnightDose(ImportAppleBase):
    """A dose taken just after midnight belongs to the previous evening's slot (how medlog already stores them)."""

    def setUp(self):
        super().setUp()
        self.map({"Fake Two": "fk_two"})
        medlog.main(["add-med", "fk_two", "--name", "Fake Two", "--kind", "daily", "--time", "09:00",
                     "--time", "22:00", "--dose", "200", "--unit", "mcg", "--start", "2030-01-01"])

    def apple(self, start, ext="n1"):
        return {"external_id": ext, "medication": "Fake Two", "status": "taken", "start": start,
                "dose": 1, "unit": "count"}

    def active(self):
        return [e for e in medlog.store_for("fk_two").events() if not e["voided"]]

    def test_after_midnight_dose_lands_in_previous_evening_slot(self):
        self.run_import([self.apple("2030-01-11T00:17:00+13:00")], apply=True)
        (e,) = self.active()
        self.assertEqual((e["date"], e["slot"], e["time"]), ("2030-01-10", "evening", "00:17"))

    def test_existing_previous_evening_record_is_corrected_to_apple_time(self):
        medlog.main(["log", "fk_two", "--date", "2030-01-10", "--slot", "evening", "--time", "23:59"])
        out = self.run_import([self.apple("2030-01-11T00:17:00+13:00")], apply=True)
        self.assertIn("corrected=1", out)
        self.assertIn("imported=0", out)
        (e,) = self.active()
        self.assertEqual((e["date"], e["slot"], e["time"]), ("2030-01-10", "evening", "00:17"))

    def test_reimport_of_an_after_midnight_dose_is_a_duplicate(self):
        data = [self.apple("2030-01-11T00:17:00+13:00")]
        self.run_import(data, apply=True)
        out = self.run_import(data, apply=True)
        self.assertIn("duplicates=1", out)
        self.assertEqual(len(self.active()), 1)

    def test_manually_logged_after_midnight_record_matches(self):
        medlog.main(["log", "fk_two", "--date", "2030-01-10", "--slot", "evening", "--time", "00:17"])
        out = self.run_import([self.apple("2030-01-11T00:17:00+13:00")])
        self.assertIn("duplicates=1", out)
        self.assertIn("corrected=0", out)

    def test_daytime_dose_is_unaffected(self):
        self.run_import([self.apple("2030-01-11T09:05:00+13:00")], apply=True)
        (e,) = self.active()
        self.assertEqual((e["date"], e["slot"]), ("2030-01-11", "morning"))


class TestAutoImport(ImportAppleBase):
    """`--auto`: apply by itself only when the import is clean and small; otherwise stay a dry run and say why."""

    def setUp(self):
        super().setUp()
        self.map({"Fake Apple": "fk_apple"})
        self.add_daily()

    def apple(self, day, ext, time="09:10", dose=50):
        return {"external_id": ext, "medication": "Fake Apple", "status": "taken",
                "start": f"2030-01-{day}T{time}:00+13:00", "dose": dose, "unit": "mg"}

    def run_auto(self, data):
        f = Path(self.tmp.name) / "auto.json"
        f.write_text(json.dumps(data))
        return self._run(["import-apple", str(f), "--auto"])[0]

    def active(self):
        return [e for e in medlog.store_for("fk_apple").events() if not e["voided"]]

    def test_clean_import_is_applied_without_the_apply_flag(self):
        out = self.run_auto([self.apple("10", "a1")])
        self.assertIn("(applied)", out)
        self.assertEqual(len(self.active()), 1)

    def test_dose_mismatch_holds_everything(self):
        medlog.main(["log", "fk_apple", "--date", "2030-01-10", "--time", "09:40", "--dose", "30"])
        out = self.run_auto([self.apple("10", "a1", dose=50), self.apple("11", "a2")])
        self.assertIn("(dry-run)", out)
        self.assertIn("held=dose_mismatch", out)
        self.assertEqual([e["date"] for e in self.active()], ["2030-01-10"])  # nothing new was written

    def test_more_changes_than_the_limit_are_held(self):
        os.environ["MEDLOG_AUTO_MAX_CHANGES"] = "2"
        self.addCleanup(os.environ.pop, "MEDLOG_AUTO_MAX_CHANGES", None)
        out = self.run_auto([self.apple(f"0{d}", f"a{d}") for d in range(1, 4)])
        self.assertIn("held=too_many_changes", out)
        self.assertEqual(self.active(), [])

    def test_time_corrections_are_applied_automatically(self):
        medlog.main(["log", "fk_apple", "--date", "2030-01-10", "--time", "09:40"])
        out = self.run_auto([self.apple("10", "a1", time="09:12")])
        self.assertIn("corrected=1", out)
        self.assertIn("(applied)", out)
        self.assertEqual([e["time"] for e in self.active()], ["09:12"])

    def test_nothing_to_do_writes_nothing_and_stays_quiet_in_the_audit_log(self):
        out = self.run_auto([])
        self.assertIn("imported=0", out)
        self.assertFalse((Path(self.tmp.name) / "auto-import.log").exists())

    def test_audit_log_records_counts_only(self):
        self.run_auto([self.apple("10", "a1")])
        line = (Path(self.tmp.name) / "auto-import.log").read_text().strip().splitlines()[-1]
        self.assertIn("imported=1", line)
        self.assertIn("result=applied", line)
        self.assertNotIn("Fake Apple", line)

    def test_rerunning_the_same_file_changes_nothing(self):
        data = [self.apple("10", "a1")]
        self.run_auto(data)
        out = self.run_auto(data)
        self.assertIn("imported=0", out)
        self.assertEqual(len(self.active()), 1)


class TestIncomparableUnits(ImportAppleBase):
    """Apple logs an injection in mL while medlog records mg: different units are not a dose mismatch, and the
    configured dose is used, exactly like an Apple "count" dose."""

    def setUp(self):
        super().setUp()
        medlog.main(["add-med", "fk_inj", "--name", "Fake Injectable", "--kind", "weekly", "--weekday", "fri",
                     "--dose", "200", "--unit", "mg", "--start", "2030-01-01"])
        self.map({"Fake Injectable": "fk_inj"})

    def item(self, time="12:33", ext="i1", dose=1, unit="mL"):
        return {"external_id": ext, "medication": "Fake Injectable", "status": "taken",
                "start": f"2030-01-11T{time}:00+13:00", "dose": dose, "unit": unit}

    def active(self):
        return [e for e in medlog.store_for("fk_inj").events() if not e["voided"]]

    def test_ml_versus_mg_is_not_a_mismatch(self):
        medlog.main(["log", "fk_inj", "--date", "2030-01-11", "--time", "12:33", "--dose", "200", "--unit", "mg"])
        out = self.run_import([self.item()])
        self.assertIn("mismatches=0", out)
        self.assertIn("duplicates=1", out)

    def test_new_dose_in_another_unit_takes_the_configured_dose(self):
        self.run_import([self.item()], apply=True)
        (e,) = self.active()
        self.assertEqual((e["dose"], e["unit"]), (200.0, "mg"))

    def test_same_unit_difference_is_still_a_mismatch(self):
        medlog.main(["log", "fk_inj", "--date", "2030-01-11", "--time", "12:33", "--dose", "100", "--unit", "mg"])
        out = self.run_import([self.item(dose=200, unit="mg")])
        self.assertIn("mismatches=1", out)

    def test_time_correction_across_units(self):
        medlog.main(["log", "fk_inj", "--date", "2030-01-11", "--time", "10:00", "--dose", "200", "--unit", "mg"])
        out = self.run_import([self.item()], apply=True)
        self.assertIn("corrected=1", out)
        (e,) = self.active()
        self.assertEqual((e["time"], e["dose"], e["unit"]), ("12:33", 200.0, "mg"))
