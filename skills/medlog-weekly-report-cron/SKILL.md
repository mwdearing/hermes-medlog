---
name: medlog-weekly-report-cron
description: Set up the weekly medication sync report as a Hermes cron recipe - imports of the last 7 days and what is still not logged.
license: Apache-2.0
compatibility: Hermes Agent cron; the medlog CLI.
---

# Weekly report (cron recipe)

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

The plugin ships no scheduled job. Propose one; the user approves it.

- Command: `medlog-weekly-report` (a console script installed with the tool; `python3 -m` will not find it when the tool was installed with pipx, because pipx isolates it). Read-only. It prints automatic imports, doses imported, time corrections and held imports from `auto-import.log` for the last 7 days (counts only), and the medications with doses not logged (`medlog missing --days 7`).
- Because a Hermes script job runs a script file, use a three-line wrapper in the Hermes scripts folder that calls the ABSOLUTE path from `command -v medlog-weekly-report` (for example `import subprocess, sys; sys.exit(subprocess.run(["<that path>"]).returncode)`); a bare name may not be on the scheduler's PATH. Create a no-agent script job weekly at the time the user chooses, **delivery `local` first**, run it once, read the output with the user, then switch delivery to a private channel of their choice.
- If it reports a held import, tell the user the counts and suggest a preview with `medlog-healthrelay-import`; never apply it yourself.
