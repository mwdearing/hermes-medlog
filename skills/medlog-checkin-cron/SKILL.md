---
name: medlog-checkin-cron
description: Set up the daily missing-dose check-in as a Hermes cron recipe - user approves, test with local delivery first, silent when nothing is missing.
license: Apache-2.0
compatibility: Hermes Agent cron; the medlog CLI.
---

# Daily check-in (cron recipe)

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

The plugin ships no scheduled job. Propose one; the user approves it.

- Script: `python3 -m medlog.recipes.checkin` (installed as `medlog-checkin`). Read-only. It prints ONE plain-text message listing slots with no record (at most 8, grouped by medication, last `MEDLOG_CHECKIN_DAYS` days, default 3) and prints nothing at all when nothing is missing, so a quiet day sends nothing.
- Create it as a no-agent script job at the time the user chooses, **with delivery `local` first**. Run it once by hand and read the output with the user. Only then switch delivery to their channel, and tell them which channel: names and slots appear in the message, so choose a private one.
- It never records anything and never advises. The user answers by logging in the Health app and syncing, or by telling the agent what they took (`medlog-logging`).
