"""Long-term history in SQLite (state_dir/history.sqlite).

events.jsonl and usage_history.jsonl are short rolling logs written by hooks (fast, append
only). `sync()` copies anything new into SQLite so history outlives their rotation, and
`stats()` answers questions over it. Event rows hold the same no-prompt-text fields as the
logs.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claudepilot import state
from claudepilot.router.classifier import tier_of_model

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL, kind TEXT NOT NULL, pane TEXT, data TEXT NOT NULL,
    UNIQUE (ts, kind, pane)
);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts);
CREATE TABLE IF NOT EXISTS usage (
    ts REAL PRIMARY KEY, five_hour REAL, seven_day REAL
);
"""
FOLLOW_WINDOW = 10 * 60  # a switch within 10 minutes of the advice counts as "followed"


def db_path() -> Path:
    return state.state_dir() / "history.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=5)
    conn.executescript(SCHEMA)
    return conn


def sync() -> tuple[int, int]:
    """Copy new events and usage snapshots into SQLite. Returns (events, snapshots) added."""
    with closing(connect()) as conn, conn:
        last_ev = conn.execute("SELECT COALESCE(MAX(ts), 0) FROM events").fetchone()[0]
        last_us = conn.execute("SELECT COALESCE(MAX(ts), 0) FROM usage").fetchone()[0]
        ev_rows = [
            (float(e["ts"]), e.get("kind", "?"), e.get("pane"),
             json.dumps({k: v for k, v in e.items() if k not in ("ts", "kind", "pane")}))
            # Overlap: concurrent hooks can append slightly out of order; UNIQUE drops repeats.
            for e in state.read_events(limit=state.MAX_EVENTS) if float(e.get("ts") or 0) > last_ev - 60
        ]
        us_rows = [
            (float(h["ts"]), h.get("five_hour"), h.get("seven_day"))
            for h in state.read_usage_history() if float(h.get("ts") or 0) > last_us
        ]
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?)", ev_rows)
        added_ev = conn.total_changes - before
        conn.executemany("INSERT OR IGNORE INTO usage VALUES (?, ?, ?)", us_rows)
    return added_ev, len(us_rows)


def load_events(since: float) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT ts, kind, pane, data FROM events WHERE ts >= ? ORDER BY ts", (since,)
        ).fetchall()
    return [{"ts": ts, "kind": kind, "pane": pane, **json.loads(data)} for ts, kind, pane, data in rows]


def load_usage(since: float) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT ts, five_hour, seven_day FROM usage WHERE ts >= ? ORDER BY ts", (since,)
        ).fetchall()
    return [{"ts": ts, "five_hour": f, "seven_day": s} for ts, f, s in rows]


@dataclass
class Day:
    date: str
    prompts: int = 0
    mix: Counter = field(default_factory=Counter)
    peak_five_hour: float | None = None
    peak_seven_day: float | None = None
    guard: int = 0


@dataclass
class Stats:
    days: list[Day]
    switch_advice: int = 0
    followed: int = 0
    delegated: int = 0
    kept_for_cache: int = 0
    escalations: int = 0
    followups: int = 0
    auto_ok: int = 0
    auto_failed: int = 0
    token_levels: Counter = field(default_factory=Counter)

    @property
    def follow_rate(self) -> float | None:
        return self.followed / self.switch_advice if self.switch_advice else None


def compute(events: list[dict[str, Any]], usage: list[dict[str, Any]]) -> Stats:
    by_day: dict[str, Day] = {}

    def day(ts: float) -> Day:
        key = time.strftime("%Y-%m-%d", time.localtime(ts))
        return by_day.setdefault(key, Day(key))

    switches_by_pane: dict[Any, list[tuple[float, str | None]]] = defaultdict(list)
    for e in events:
        if e["kind"] == "model_switch":
            switches_by_pane[e.get("pane")].append((e["ts"], tier_of_model(e.get("to_model"))))
        elif e["kind"] == "auto_route":
            switches_by_pane[e.get("pane")].append(
                (e["ts"], e.get("to") if e.get("status") == "ok" else None))

    st = Stats(days=[])
    for e in events:
        kind = e["kind"]
        if kind == "route":
            d = day(e["ts"])
            d.prompts += 1
            if e.get("current"):
                d.mix[e["current"]] += 1
            action, rule = e.get("action"), e.get("rule")
            if action == "switch":
                st.switch_advice += 1
                rec = e.get("recommended")
                if any(e["ts"] <= ts <= e["ts"] + FOLLOW_WINDOW and tier == rec
                       for ts, tier in switches_by_pane.get(e.get("pane"), [])):
                    st.followed += 1
            elif action == "delegate":
                st.delegated += 1
            elif action == "stay":
                st.kept_for_cache += 1
            if rule == "context-escalate":
                st.escalations += 1
            elif rule == "context-followup":
                st.followups += 1
        elif kind == "auto_route":
            if e.get("status") == "ok":
                st.auto_ok += 1
            else:
                st.auto_failed += 1
        elif kind == "tokens":
            st.token_levels[e.get("level") or "skip"] += 1
        elif kind == "guard":
            day(e["ts"]).guard += 1

    for u in usage:
        d = day(u["ts"])
        if u.get("five_hour") is not None:
            d.peak_five_hour = max(d.peak_five_hour or 0.0, u["five_hour"])
        if u.get("seven_day") is not None:
            d.peak_seven_day = max(d.peak_seven_day or 0.0, u["seven_day"])

    st.days = [by_day[k] for k in sorted(by_day)]
    return st


def stats(days: int = 30, now: float | None = None) -> Stats:
    sync()
    since = (now or time.time()) - days * 86_400
    return compute(load_events(since), load_usage(since))


def delete() -> None:
    db_path().unlink(missing_ok=True)
