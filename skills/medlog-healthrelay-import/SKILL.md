---
name: medlog-healthrelay-import
description: Import dose events from a HealthRelay receiver database into medlog - preview first, read-only on the database, held imports explained.
license: Apache-2.0
compatibility: The medlog CLI and a HealthRelay receiver SQLite database (read access).
---

# Import from a HealthRelay receiver

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

`medlog import-bridge --db <receiver.sqlite>` reads medication dose events straight from the receiver database (opened read-only) and gives them to the same matching, duplicate and correction rules as `import-apple`.

1. **Preview.** Without flags it is a dry run: it prints counts (`imported`, `duplicates`, `unmapped`, `mismatches`, `corrected`) and writes nothing. Read the counts to the user; names are shown only in private.
2. **Apply.** `--apply` writes. The cursor (newest `updated_at` seen) then moves forward, and only then. `--auto` is for unattended runs: it applies only when there is no dose mismatch and no more than `MEDLOG_AUTO_MAX_CHANGES` (default 12) changes; otherwise it is a dry run ("held") and the cursor stays put.
3. **Held.** Tell the user the counts and the reason (`dose_mismatch` or `too_many_changes`). Never apply a held import yourself; the user decides after reading a preview.
4. **Unmapped.** Events with no mapping are counted and skipped; they do not block the cursor. After the user confirms a mapping (`medlog-register-medication`), re-read with `--since YYYY-MM-DD --apply`; duplicates are skipped.
5. **Health-app time wins.** When a matching hand-logged dose has a different time, the import writes the Health-app record and voids the old one with a reason. Nothing is deleted or edited.
6. **Optional automation.** A systemd user path unit that watches the receiver database and runs `medlog import-bridge --db <path> --auto` is the user's choice; propose it, show the unit text, install only with their approval, and test with a manual run first.
