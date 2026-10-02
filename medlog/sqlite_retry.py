"""Retry a read-only query against the receiver database while the receiver is writing.

A read-only connection cannot roll back a rollback journal, so a read that meets the receiver mid-write can fail with
``attempt to write a readonly database``, ``database is locked`` or ``disk I/O error``. These are transient: close,
wait and reopen. The database is never opened writable and never ``immutable``.
"""
from __future__ import annotations

import sqlite3
import time

DELAYS = (0.2, 0.4, 0.8, 1.6)  # 5 attempts over about 3 seconds
TRANSIENT = ("readonly database", "database is locked", "disk i/o error")


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(t in str(exc).lower() for t in TRANSIENT)


def retry_read(fn, sleep=None):
    """Call ``fn`` (which opens, queries and closes); retry on a transient error, re-raise after the last attempt."""
    sleep = sleep or time.sleep
    for delay in DELAYS:
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_transient(exc):
                raise
        sleep(delay)
    return fn()
