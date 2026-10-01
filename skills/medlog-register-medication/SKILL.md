---
name: medlog-register-medication
description: Register a new medication end to end - registry entry, Apple Health name map, backfill and the check-med verification.
license: Apache-2.0
compatibility: The medlog CLI; a HealthRelay receiver database for the import steps.
---

# Registering a new medication

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

**First, run `medlog doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database path is the `bridge_db` setting (config key, or `MEDLOG_BRIDGE_DB`), and `--db` is only an override.

The user decides every value (name, dose, unit, kind, times, weekday, start date). Use placeholders below, never guessed values.

1. **Apple Health.** The user adds the medication in Health > Medications with its schedule, and lets HealthRelay read it (Health > Profile > Privacy > Apps; access can be per medication).
2. **Register.** `medlog add-med <id> --name "<name as the Health app shows it>" --dose D --unit U --kind daily|weekly|prn [--time HH:MM ...] [--weekday day] --start YYYY-MM-DD`. Confirm the values with the user first. A daily medication needs at least one `--time`; a weekly one a `--weekday`.
3. **Map.** After the first dose is logged in Health and synced, run `medlog unmapped --days 14` (read-only): it lists display names and `concept <id>` keys that reach no registered medication. Show names only to the user in private. Never run `map-apple` until the user confirms which name or concept id goes with which registered id: `medlog map-apple "<name or concept <id>>" <id>` (it refuses an unknown id and an existing key unless `--replace`).
4. **Backfill.** `medlog import-bridge --since YYYY-MM-DD` is a preview; add `--apply` after the user has read the counts. Duplicates are skipped by design.
5. **Verify.** `medlog check-med <id>` (exit 0 = complete; one line per problem) and `medlog missing --days 3`. Report counts and "complete" or the problem lines.
6. **Stopping.** `medlog stop-med <id> --date D` keeps history. `remove-med` is only for a mistaken entry and refuses when records exist.
