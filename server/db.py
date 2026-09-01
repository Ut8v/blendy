"""SQLite data layer. WAL mode, migrations from day one.

Queried and aggregated things live here (assets index, incidents, eval runs,
shot render state, takes). Specs, bible, breakdown, skills and profiles stay as
files: they are read, diffed and hand-edited. The compiler never imports this.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "blendy.sqlite"

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE assets (
        hash        TEXT PRIMARY KEY,
        source      TEXT NOT NULL,
        ref         TEXT NOT NULL,
        license     TEXT,
        url         TEXT,
        path        TEXT NOT NULL,
        ingested_at REAL,
        UNIQUE (source, ref)
    );
    CREATE TABLE asset_index (
        hash            TEXT PRIMARY KEY REFERENCES assets(hash),
        class           TEXT NOT NULL,
        profile_version INTEGER NOT NULL,
        landmark_names  TEXT NOT NULL,      -- JSON list
        flags           TEXT NOT NULL,      -- JSON object
        height          REAL
    );
    CREATE TABLE incidents (
        id          TEXT PRIMARY KEY,
        shot        TEXT NOT NULL,
        agent       TEXT NOT NULL,
        category    TEXT,                   -- schema | tool | judgment | NULL until triaged
        expected    TEXT NOT NULL,
        observed    TEXT NOT NULL,
        resolution  TEXT,                   -- JSON {kind, ref} or NULL
        session     TEXT,
        created     REAL NOT NULL
    );
    CREATE TABLE eval_runs (
        id          TEXT PRIMARY KEY,
        commit_ref  TEXT,
        skill_diff  TEXT,
        started     REAL NOT NULL,
        finished    REAL,
        passed      INTEGER
    );
    CREATE TABLE eval_results (
        run         TEXT NOT NULL REFERENCES eval_runs(id),
        shot        TEXT NOT NULL,
        metric      TEXT NOT NULL,
        value       REAL NOT NULL,
        PRIMARY KEY (run, shot, metric)
    );
    CREATE TABLE shot_state (
        shot_id      TEXT PRIMARY KEY,
        spec_hash    TEXT,
        render_state TEXT NOT NULL DEFAULT 'pending',   -- pending|rendering|done|failed|stale
        frames_done  INTEGER NOT NULL DEFAULT 0,
        frames_total INTEGER NOT NULL DEFAULT 0,
        last_render  REAL,
        error        TEXT
    );
    CREATE TABLE takes (
        id          TEXT PRIMARY KEY,
        shot_id     TEXT NOT NULL,
        mode        TEXT NOT NULL,          -- live | keyframe
        raw_path    TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        promoted_to TEXT
    );
    """),
    (2, """
    CREATE TABLE corrections (
        id          TEXT PRIMARY KEY,
        scope       TEXT NOT NULL,
        situation   TEXT NOT NULL,
        wrong       TEXT NOT NULL,
        right       TEXT NOT NULL,
        supersedes  TEXT NOT NULL,          -- JSON list
        created     REAL NOT NULL,
        hits        INTEGER NOT NULL DEFAULT 0,
        last_hit    REAL,
        active      INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX incidents_shot_agent ON incidents(agent, category, shot);
    """),
]


class Database:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self.migrate()

    # --- migrations ---------------------------------------------------------

    def version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> list[int]:
        applied = []
        current = self.version()
        for number, sql in MIGRATIONS:
            if number <= current:
                continue
            # executescript commits any open transaction first, so the script
            # carries its own BEGIN/COMMIT: a failed migration rolls back whole.
            self._conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={number};\nCOMMIT;")
            applied.append(number)
        return applied

    # --- plumbing --------------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.tx():
            self._conn.execute(sql, params)

    def close(self) -> None:
        self._conn.close()

    # --- assets ------------------------------------------------------------------

    def upsert_asset(self, h: str, source: str, ref: str, path: str, license: str | None,
                     url: str | None) -> None:
        self.execute("""INSERT INTO assets(hash, source, ref, license, url, path)
                        VALUES (?,?,?,?,?,?)
                        ON CONFLICT(hash) DO UPDATE SET path=excluded.path,
                            license=excluded.license, url=excluded.url""",
                     (h, source, ref, license, url, path))

    def index_profile(self, profile: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("UPDATE assets SET ingested_at=? WHERE hash=?", (time.time(), profile["hash"]))
            c.execute("""INSERT INTO asset_index(hash, class, profile_version, landmark_names, flags, height)
                         VALUES (?,?,?,?,?,?)
                         ON CONFLICT(hash) DO UPDATE SET class=excluded.class,
                            profile_version=excluded.profile_version,
                            landmark_names=excluded.landmark_names, flags=excluded.flags,
                            height=excluded.height""",
                      (profile["hash"], profile["class"], profile["profile_version"],
                       json.dumps(sorted(profile.get("landmarks", {}))),
                       json.dumps(profile.get("flags", {})), profile.get("height")))

    def asset_by_ref(self, source: str, ref: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM assets WHERE source=? AND ref=?", (source, ref))

    def stale_profiles(self, current_version: int) -> list[str]:
        return [r["hash"] for r in self.query(
            "SELECT hash FROM asset_index WHERE profile_version < ?", (current_version,))]

    # --- incidents ------------------------------------------------------------------

    def next_incident_id(self) -> str:
        row = self.one("SELECT COUNT(*) AS n FROM incidents")
        return f"INC-{(row['n'] if row else 0) + 1:04d}"

    def insert_incident(self, inc: dict[str, Any]) -> None:
        self.execute("""INSERT INTO incidents(id, shot, agent, category, expected, observed,
                        resolution, session, created) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (inc["id"], inc["shot"], inc["agent"], inc.get("category"),
                      inc["expected"], inc["observed"],
                      json.dumps(inc["resolution"]) if inc.get("resolution") else None,
                      inc.get("session"), inc.get("created", time.time())))

    def recurring_patterns(self, min_shots: int = 3) -> list[dict[str, Any]]:
        """The n>=3 threshold is a GROUP BY. Incidents from one session count once."""
        return self.query("""
            SELECT agent, COALESCE(category, 'untriaged') AS category,
                   COUNT(DISTINCT shot) AS shots, COUNT(DISTINCT COALESCE(session, id)) AS sessions,
                   GROUP_CONCAT(id) AS ids
            FROM incidents WHERE resolution IS NULL
            GROUP BY agent, category
            HAVING COUNT(DISTINCT shot) >= ? AND COUNT(DISTINCT COALESCE(session, id)) >= ?
            ORDER BY shots DESC""", (min_shots, min_shots))

    # --- shot render state ---------------------------------------------------------

    def set_shot_state(self, shot_id: str, **fields: Any) -> None:
        cols = ["shot_id"] + list(fields)
        sets = ", ".join(f"{k}=excluded.{k}" for k in fields)
        self.execute(f"""INSERT INTO shot_state({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})
                         ON CONFLICT(shot_id) DO UPDATE SET {sets}""",
                     (shot_id, *fields.values()))

    def shot_states(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM shot_state ORDER BY shot_id")
