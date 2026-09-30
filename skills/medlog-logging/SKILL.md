---
name: medlog-logging
description: Record doses with medlog - only what the user says, never invented times or doses; void instead of edit; counts in shared channels.
license: Apache-2.0
compatibility: The medlog CLI.
---

# Logging doses

Informational only, not medical advice. This tool never infers a dose, time, status or schedule, and it gives no dosing, interaction or schedule advice. Ask the user for every value. Questions about doses, missed doses, side effects or interactions go to their prescriber or pharmacist.

Rules
1. **Record only what the user said.** Status, time and dose come from their words. Never guess a time; never fill in a dose they did not state (a registered default dose is used only when they say they took "it"). No time given: log without one (`time_source` is then `unspecified`).
2. **One record per slot.** A duplicate is refused. Use `--force` only if the user confirms it really was a second dose, and then with `--status extra`.
3. **Corrections are `void`, never edits.** `medlog void <id> --reason "..."` (the short id printed by `log` works). Never edit or delete `events.jsonl` or `medications.json` by hand, even to fix a failed void: stop and tell the user.
4. **Registered medications only.** For an unknown one use the `medlog-register-medication` skill; do not add it yourself from a guess.
5. **Never advise.** No "safe to double up", no interaction claims, no "you should skip". For missed doses, dose changes and interactions: "ask your prescriber or pharmacist". If the user mentions thoughts of self-harm or severe side effects, tell them plainly to contact their prescriber now, or their local emergency number if they are in danger.
6. **Privacy.** In shared or public channels report aggregates ("3 of 4 slots recorded, 1 missing"); give names and doses only when the user asks, in a private conversation. Health data stays on the user's machine; use a local model or one they trust with it.

Commands: `medlog log <id> [--status taken|skipped|missed|extra] [--time HH:MM | --at ISO] [--date YYYY-MM-DD] [--note T] [--dry-run]`, `medlog show --days N`, `medlog missing --days N`, `medlog status`, `medlog untimed --days N`. Future dates and times are refused.
