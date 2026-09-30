# hermes-medlog

A deterministic medication log for your own use, packaged as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin (skills) plus a small command line tool, `medlog`. You (or your agent, on your word) record what you took, skipped or missed; `medlog` shows what is still missing; and it can import dose events from your own [HealthRelay](https://github.com/mwdearing/health-relay) receiver.

> Informational only, not medical advice. `medlog` never infers a dose, time, status or schedule, and gives no dosing, interaction or schedule advice. Dose changes, missed doses, side effects and interactions are questions for your prescriber or pharmacist.

Part of a small set that work together: [HealthRelay](https://github.com/mwdearing/health-relay) (app + receiver), [hermes-healthrelay](https://github.com/mwdearing/hermes-healthrelay) (read-only access for your agent), [hermes-health-insights](https://github.com/mwdearing/hermes-health-insights) (analysis; its optional `medication_adherence` module reads `medlog --json missing`) and this one.

Setting up the whole chain (app and receiver, hermes-healthrelay, hermes-health-insights, this plugin)? Follow the [Full setup guide](https://github.com/mwdearing/health-relay/blob/main/docs/full-setup.md): one ordered walkthrough with a check after each step.

## What it does
| Command | Purpose |
| --- | --- |
| `medlog add-med / list-meds / stop-med / remove-med` | The registry: name, dose, unit, kind (daily, weekly, as needed), times, weekday, start |
| `medlog log <id>` | Record `taken`, `skipped`, `missed` or `extra`. The logging moment is always the system clock; the dose time is stored only when you give it |
| `medlog missing / status / untimed` | Expected slots with no record; today's view; doses recorded without a time |
| `medlog void <id> --reason ...` | Correct a mistake (nothing is ever deleted or edited) |
| `medlog show --days N --format text\|json\|csv` | Your history, for you or your clinician |
| `medlog import-bridge --db <receiver.sqlite>` | Import dose events from a HealthRelay receiver (read-only on the database; preview first) |
| `medlog import-apple FILE` | Import an Apple Health medication export file |
| `medlog unmapped / map-apple / check-med` | Register a new medication end to end |

It refuses future times and dates, unknown medications and duplicate slots (use `--force` for a real second dose). Exit codes: 0 ok, 2 validation error, 3 duplicate.

## Install
1. Install the tool and try it on made-up data (nothing real is touched):
   ```bash
   pipx install git+https://github.com/mwdearing/hermes-medlog
   export MEDLOG_HOME=$(mktemp -d)
   medlog add-med demo --name "Demo medicine" --dose 10 --unit mg --time 08:00
   medlog log demo --time 08:05
   medlog missing --days 3
   ```
2. Install the plugin (skills for setup, logging, registering a medication, the HealthRelay import and the two cron recipes):
   ```bash
   hermes plugins install mwdearing/hermes-medlog --no-enable
   hermes plugins enable medlog
   ```
   Then ask your agent to follow the `medlog-setup` skill.

## Settings
Each setting resolves as: environment variable, then the JSON config file, then the default. Config file: `$MEDLOG_CONFIG`, else `~/.config/medlog/config.json`. An invalid file is an error, never silently ignored.

| Setting | Environment | Config key | Default |
| --- | --- | --- | --- |
| Data folder | `MEDLOG_HOME` | `data_dir` | `$XDG_DATA_HOME/medlog`, else `~/.local/share/medlog` |
| Time zone | `MEDLOG_TZ` | `timezone` | the system zone |
| Apple name map | `MEDLOG_APPLE_MAP` | `apple_map` | `<data folder>/apple_med_map.json` |

## HealthRelay
Point `import-bridge` at the receiver's SQLite database (`--db`). It opens the file read-only, previews by default (`--apply` writes, `--auto` is the unattended mode with safety holds), and remembers a cursor that only moves forward after a successful apply. See the `medlog-healthrelay-import` skill; an optional systemd path unit can run it after every sync.

## Scheduled reports
Two read-only, plain-text recipes for Hermes cron: `medlog-checkin` (silent unless something is missing) and `medlog-weekly-report`. The plugin ships no job; the skills walk you through creating one, testing it with local delivery first, and choosing a private channel.

## Privacy and trust
- Everything runs on your machine. Nothing is uploaded, and `medlog` has no network code.
- Your data folder holds medication records. Keep it private and out of git; use a local model, or one you trust with health data, and keep names and doses out of public channels.
- Enabling any Hermes plugin grants its skills full trust. This plugin contains only skills; the CLI is installed separately.

## Uninstall
```bash
hermes plugins uninstall medlog
pipx uninstall medlog
```
Your data folder (`~/.local/share/medlog` unless you changed it) and `~/.config/medlog/` are yours and are not removed.

## Limits
- Not affiliated with Apple. Not a medical device.
- Single user, single machine; no sync between devices.

## Development
`python3 -m pytest -q` runs the test suite (synthetic data only). This repository is generated from a private source; report issues here.
