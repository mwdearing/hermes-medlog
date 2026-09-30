"""Settings resolution: environment, then config file, then default. Synthetic data only."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import medlog
from medlog import config

KEYS = ("MEDLOG_CONFIG", "MEDLOG_HOME", "MEDLOG_TZ", "MEDLOG_STORE", "MEDLOG_NOW", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "TZ")


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = {k: os.environ.get(k) for k in KEYS}
        for k in KEYS:
            os.environ.pop(k, None)
        self.cfg = Path(self.tmp.name) / "config.json"
        os.environ["MEDLOG_CONFIG"] = str(self.cfg)
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write(self, data):
        self.cfg.write_text(json.dumps(data) if not isinstance(data, str) else data)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = medlog.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_default_data_dir_is_xdg_and_not_a_personal_path(self):
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        self.assertEqual(medlog.data_dir(), Path(self.tmp.name) / "medlog")
        del os.environ["XDG_DATA_HOME"]
        self.assertEqual(medlog.data_dir(), Path("~/.local/share/medlog").expanduser())

    def test_default_timezone_is_the_system_zone(self):
        os.environ["TZ"] = "Europe/Paris"
        self.assertEqual(str(medlog.tz()), "Europe/Paris")

    def test_config_file_beats_default(self):
        self.write({"data_dir": self.tmp.name + "/d", "timezone": "Asia/Tokyo"})
        self.assertEqual(medlog.data_dir(), Path(self.tmp.name) / "d")
        self.assertEqual(str(medlog.tz()), "Asia/Tokyo")

    def test_environment_beats_config_file(self):
        self.write({"data_dir": self.tmp.name + "/d", "timezone": "Asia/Tokyo"})
        os.environ["MEDLOG_HOME"], os.environ["MEDLOG_TZ"] = self.tmp.name + "/e", "Europe/Paris"
        self.assertEqual(medlog.data_dir(), Path(self.tmp.name) / "e")
        self.assertEqual(str(medlog.tz()), "Europe/Paris")

    def test_missing_config_file_is_fine(self):
        self.assertEqual(config.load(), {})

    def test_invalid_config_fails_closed_instead_of_using_defaults(self):
        self.write("{not json")
        code, _, err = self.cli("list-meds")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_unknown_key_is_an_error(self):
        self.write({"data_dri": "/x"})
        code, _, err = self.cli("list-meds")
        self.assertEqual(code, 2)
        self.assertIn("data_dri", err)

    def test_unknown_store_is_a_clear_error(self):
        os.environ["MEDLOG_HOME"] = self.tmp.name
        code, _, err = self.cli("add-med", "fk", "--name", "Fk", "--time", "08:00", "--store", "nosuch")
        self.assertEqual(code, 2)
        self.assertIn("unknown store", err)
        self.assertIn("jsonl", err)

    def test_unloadable_extension_is_an_error(self):
        os.environ["MEDLOG_HOME"] = self.tmp.name
        self.write({"extensions": ["no_such_module_xyz"]})
        medlog.core._extensions_loaded = False
        try:
            code, _, err = self.cli("add-med", "fk", "--name", "Fk", "--time", "08:00")
        finally:
            medlog.core._extensions_loaded = False
        self.assertEqual(code, 2)
        self.assertIn("no_such_module_xyz", err)


if __name__ == "__main__":
    unittest.main()
