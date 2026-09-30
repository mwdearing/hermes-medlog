"""Settings resolution for medlog.

Every setting resolves in this order: environment variable, then the config file, then the built-in default.
The config file is JSON: ``$MEDLOG_CONFIG``, else ``$XDG_CONFIG_HOME/medlog/config.json``, else
``~/.config/medlog/config.json``. A missing file is fine; an unreadable or invalid one is an error, so a typo
can never silently send records to a different data directory.

Keys: data_dir, timezone, extensions, v1_path, medlog_bin, notify_command, apple_map.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

KEYS = {"data_dir", "timezone", "extensions", "v1_path", "medlog_bin", "notify_command", "apple_map"}

_warned = False


class ConfigError(Exception):
    """Raised for an invalid config file; medlog.core turns it into a MedlogError (exit code 2)."""


def config_path() -> Path:
    raw = os.environ.get("MEDLOG_CONFIG")
    if raw:
        return Path(raw).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "medlog" / "config.json"


def load() -> dict:
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config file must be a JSON object: {path}")
    unknown = sorted(set(data) - KEYS)
    if unknown:
        raise ConfigError(f"unknown key(s) in {path}: {', '.join(unknown)}")
    return data


def get(key: str, env: str | None = None, default=None):
    """Environment variable ``env`` (when set and non-empty), else the config file, else ``default``."""
    if env:
        raw = os.environ.get(env)
        if raw is not None and raw != "":
            return raw
    value = load().get(key)
    return default if value is None else value


def system_timezone() -> str:
    """The host's IANA zone name from /etc/localtime or TZ; UTC (with one warning) when it cannot be told."""
    global _warned
    tz_env = os.environ.get("TZ", "").lstrip(":")
    if tz_env and "/" in tz_env and not tz_env.startswith("/"):
        return tz_env
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        target = ""
    marker = "zoneinfo/"
    if marker in target:
        return target.split(marker, 1)[1]
    if not _warned:
        _warned = True
        print("medlog: cannot detect the system time zone; using UTC (set MEDLOG_TZ or 'timezone' in the config file)",
              file=sys.stderr)
    return "UTC"


def default_data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return str(Path(base) / "medlog")


def timezone_name() -> str:
    return str(get("timezone", "MEDLOG_TZ") or system_timezone())


def data_dir_setting() -> str:
    return str(get("data_dir", "MEDLOG_HOME") or default_data_dir())


def extensions() -> list[str]:
    value = load().get("extensions") or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError("config key 'extensions' must be a list of module names")
    return value
