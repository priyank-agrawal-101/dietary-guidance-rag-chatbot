"""Atomic persistent reservations; no questions, prompts, keys or outputs are stored."""

import math
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

from dietary_rag.answering.contracts import AnswerModelUnavailable


class UsageLimitExceeded(AnswerModelUnavailable):
    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = max(1, math.ceil(retry_after_seconds))
        super().__init__("generation usage limit exceeded")


class TokenBudgetExceeded(AnswerModelUnavailable):
    pass


WINDOWS = (("minute", 60), ("hour", 3600), ("day", 86400))


class UsageLedger:
    def __init__(self, path, settings, *, clock=time.time):
        self.path, self.settings, self.clock = path, settings, clock

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS calls "
                "(id TEXT PRIMARY KEY, started REAL NOT NULL, tokens INTEGER NOT NULL, "
                "pending INTEGER NOT NULL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS call_time ON calls(started)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY, until REAL NOT NULL)"
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM calls WHERE started <= ?", (self.clock() - 86400,))
            yield connection

    def reserve(self, tokens):
        with self._connection() as connection:
            now = self.clock()
            cooldown = connection.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
            if cooldown and cooldown[0] > now:
                raise UsageLimitExceeded(cooldown[0] - now)
            retry = 0
            for name, duration in WINDOWS:
                rows = connection.execute(
                    "SELECT started, tokens FROM calls WHERE started > ? ORDER BY started",
                    (now - duration,),
                ).fetchall()
                count, total = len(rows), sum(row[1] for row in rows)
                max_requests = getattr(self.settings, f"requests_per_{name}")
                max_tokens = getattr(self.settings, f"tokens_per_{name}")
                if tokens > max_tokens:
                    raise TokenBudgetExceeded("call exceeds a token window")
                for started, used in rows:
                    if count + 1 <= max_requests and total + tokens <= max_tokens:
                        break
                    retry = max(retry, started + duration - now)
                    count -= 1
                    total -= used
            if retry > 0:
                raise UsageLimitExceeded(retry)
            identifier = str(uuid4())
            connection.execute("INSERT INTO calls VALUES (?, ?, ?, 1)", (identifier, now, tokens))
        return identifier

    def settle(self, identifier, actual_tokens=None):
        # Unknown usage (timeout/outage/malformed response) retains the full reservation.
        with self._connection() as connection:
            if actual_tokens is None:
                connection.execute("UPDATE calls SET pending=0 WHERE id=?", (identifier,))
            else:
                connection.execute(
                    "UPDATE calls SET tokens=?, pending=0 WHERE id=?", (actual_tokens, identifier)
                )

    def cooldown(self, seconds):
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO cooldown VALUES (1, ?) ON CONFLICT(id) "
                "DO UPDATE SET until=MAX(until, excluded.until)",
                (self.clock() + seconds,),
            )

    def summary(self):
        with self._connection() as connection:
            now = self.clock()
            windows = {}
            for name, duration in WINDOWS:
                count, tokens, pending = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(tokens),0), COALESCE(SUM(pending),0) "
                    "FROM calls WHERE started > ?",
                    (now - duration,),
                ).fetchone()
                windows[name] = {
                    "requests": count,
                    "tokens": tokens,
                    "pending_calls": pending,
                    "request_limit": getattr(self.settings, f"requests_per_{name}"),
                    "token_limit": getattr(self.settings, f"tokens_per_{name}"),
                }
            row = connection.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
        return {
            "window_type": "rolling",
            "windows": windows,
            "retry_after_seconds": max(0, math.ceil(row[0] - now)) if row else 0,
        }
