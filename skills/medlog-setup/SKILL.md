---
name: medlog-setup
description: Set up medlog - install the CLI, check the config file, data folder and time zone, and try it with a made-up medication before real data.
license: Apache-2.0
compatibility: Python 3.11+, no other dependencies.
---

# medlog setup

The plugin only holds skills. The tool is the `medlog` command line program, installed separately. Work through the list in order and confirm each result before the next step.

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

**First, run `medlog doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database path is the `bridge_db` setting (config key, or `MEDLOG_BRIDGE_DB`), and `--db` is only an override.

1. **Installed?** Run `medlog --version`. If missing: install it from this plugin's own copy, which Hermes already fetched at the installed commit: `pipx install "${HERMES_HOME:-$HOME/.hermes}/plugins/medlog"` (or `uv tool install` / a venv from the same path), then run `--version` again. Do not install from the repository's latest commit.
2. **Where does it write?** By default the data folder is `$XDG_DATA_HOME/medlog` (else `~/.local/share/medlog`) and the time zone is the system zone. Override with `MEDLOG_HOME` / `MEDLOG_TZ`, or a JSON config file at `~/.config/medlog/config.json` (keys: `data_dir`, `timezone`, and `bridge_db` for the HealthRelay receiver database). Environment beats the file, the file beats the defaults. An invalid config file is an error, never silently ignored.
3. **Try it on made-up data first.** Use a scratch folder so nothing real is touched: `export MEDLOG_HOME=$(mktemp -d)`, then `medlog add-med demo --name "Demo medicine" --time 08:00`, `medlog log demo --time 08:05`, `medlog missing --days 3`, `medlog status`.
4. **Real data.** Only when the user asks. The data folder holds health records: keep it private, never put it in git, and never paste its contents into a public channel.
5. Next skills: `medlog-logging` (record doses), `medlog-register-medication`, `medlog-healthrelay-import`.
