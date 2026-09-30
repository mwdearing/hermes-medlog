---
name: medlog-weekly-report-cron
description: Set up the weekly medication sync report as a Hermes cron recipe - imports of the last 7 days and what is still not logged.
license: Apache-2.0
compatibility: Hermes Agent cron; the medlog CLI.
---

# Weekly report (cron recipe)

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

The plugin ships no scheduled job. Propose one; the user approves it.

- Script: `python3 -m medlog.recipes.weekly_report` (installed as `medlog-weekly-report`). Read-only. It prints automatic imports, doses imported, time corrections and held imports from `auto-import.log` for the last 7 days (counts only), and the medications with doses not logged (`medlog missing --days 7`).
- Create a no-agent script job weekly at the time the user chooses, **delivery `local` first**, run it once, read the output with the user, then switch delivery to a private channel of their choice.
- If it reports a held import, tell the user the counts and suggest a preview with `medlog-healthrelay-import`; never apply it yourself.
