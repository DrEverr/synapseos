"""SQLite-backed instance store — single source of truth for ontologies, prompts, and metadata.

Each SynapseOS instance has one SQLite database that stores:
- Generated ontology (entity types + relationship types), versioned
- Generated prompts (entity extraction, relationship extraction, reasoning, etc.), versioned
- Bootstrap metadata (source documents, domain description, timestamps)
- Instance configuration overrides

This makes it trivial to:
- Backup an entire instance (copy one .db file)
- Switch between ontology versions (just change active_version)
- Test different field configurations side by side
- Roll back to a previous ontology/prompt set
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ontology_versions (
    version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    domain        TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    is_active     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entity_types (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id     INTEGER NOT NULL REFERENCES ontology_versions(version_id),
    type_name      TEXT NOT NULL,
    description    TEXT NOT NULL,
    properties     TEXT DEFAULT '{}',
    UNIQUE(version_id, type_name)
);

CREATE TABLE IF NOT EXISTS relationship_types (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id     INTEGER NOT NULL REFERENCES ontology_versions(version_id),
    type_name      TEXT NOT NULL,
    description    TEXT NOT NULL,
    properties     TEXT DEFAULT '{}',
    UNIQUE(version_id, type_name)
);

CREATE TABLE IF NOT EXISTS prompts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id     INTEGER NOT NULL REFERENCES ontology_versions(version_id),
    prompt_key     TEXT NOT NULL,
    prompt_text    TEXT NOT NULL,
    description    TEXT DEFAULT '',
    UNIQUE(version_id, prompt_key)
);

CREATE TABLE IF NOT EXISTS bootstrap_sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id     INTEGER NOT NULL REFERENCES ontology_versions(version_id),
    source_type    TEXT NOT NULL,
    source_path    TEXT NOT NULL,
    page_count     INTEGER DEFAULT 0,
    sample_text    TEXT DEFAULT '',
    processed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id       TEXT PRIMARY KEY,
    name             TEXT DEFAULT '',
    started_at       TEXT NOT NULL,
    domain           TEXT DEFAULT '',
    summary          TEXT DEFAULT '',
    compacted_turns  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reasoning_episodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT REFERENCES chat_sessions(session_id),
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL,
    steps_taken      INTEGER DEFAULT 0,
    empty_results    INTEGER DEFAULT 0,
    timed_out        INTEGER DEFAULT 0,
    max_steps_reached INTEGER DEFAULT 0,
    doom_loop_triggered INTEGER DEFAULT 0,
    elapsed_seconds  REAL DEFAULT 0.0,
    section_ids      TEXT DEFAULT '[]',
    actions_log      TEXT DEFAULT '[]',
    confidence       REAL DEFAULT 0.0,
    groundedness     REAL DEFAULT 0.0,
    completeness     REAL DEFAULT 0.0,
    assessment_reasoning TEXT DEFAULT '',
    assessment_gaps  TEXT DEFAULT '[]',
    entities_added   INTEGER DEFAULT 0,
    rels_added       INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type      TEXT NOT NULL,
    action_id        TEXT DEFAULT '',
    action_label     TEXT DEFAULT '',
    item_type        TEXT NOT NULL,
    item_name        TEXT NOT NULL,
    item_detail      TEXT DEFAULT '',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extracted_tables (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_doc       TEXT NOT NULL,
    source_section   TEXT DEFAULT '',
    table_name       TEXT NOT NULL,
    columns_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extracted_table_rows (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id         INTEGER NOT NULL REFERENCES extracted_tables(id),
    row_index        INTEGER NOT NULL,
    row_json         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_table_rows_table_id ON extracted_table_rows(table_id);
CREATE INDEX IF NOT EXISTS idx_extracted_tables_name ON extracted_tables(table_name);
"""


class InstanceStore:
    """SQLite-backed storage for a single SynapseOS instance."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply schema migrations for databases created by older versions."""
        # Check if reasoning_episodes has session_id column
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(reasoning_episodes)").fetchall()
        }
        if "session_id" not in cols:
            self._conn.execute(
                "ALTER TABLE reasoning_episodes ADD COLUMN session_id TEXT DEFAULT ''"
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Metadata ──────────────────────────────────────────────

    def get_meta(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def is_bootstrapped(self) -> bool:
        return self.get_meta("bootstrapped") == "true"

    def mark_bootstrapped(self, domain: str = "") -> None:
        self.set_meta("bootstrapped", "true")
        self.set_meta("bootstrap_timestamp", datetime.now(timezone.utc).isoformat())
        if domain:
            self.set_meta("domain", domain)

    # ── Ontology Versions ─────────────────────────────────────

    def create_ontology_version(
        self,
        name: str,
        description: str = "",
        domain: str = "",
        activate: bool = True,
    ) -> int:
        """Create a new ontology version. Optionally set it as active."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO ontology_versions (name, description, domain, created_at, is_active) "
            "VALUES (?, ?, ?, ?, 0)",
            (name, description, domain, now),
        )
        version_id = cur.lastrowid
        assert version_id is not None
        if activate:
            self.activate_version(version_id)
        self._conn.commit()
        logger.info("Created ontology version %d: '%s'", version_id, name)
        return version_id

    def activate_version(self, version_id: int) -> None:
        """Set a version as the active one (deactivates all others)."""
        self._conn.execute("UPDATE ontology_versions SET is_active = 0")
        self._conn.execute(
            "UPDATE ontology_versions SET is_active = 1 WHERE version_id = ?",
            (version_id,),
        )
        self._conn.commit()

    def get_active_version_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT version_id FROM ontology_versions WHERE is_active = 1"
        ).fetchone()
        return row["version_id"] if row else None

    def list_versions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT version_id, name, description, domain, created_at, is_active "
            "FROM ontology_versions ORDER BY version_id"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Entity Types ──────────────────────────────────────────

    def store_entity_type(
        self, version_id: int, type_name: str, description: str, properties: dict | None = None
    ) -> None:
        props_json = json.dumps(properties or {}, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO entity_types (version_id, type_name, description, properties) "
            "VALUES (?, ?, ?, ?)",
            (version_id, type_name, description, props_json),
        )
        self._conn.commit()

    def store_entity_types_batch(
        self, version_id: int, types: dict[str, str], properties: dict[str, dict] | None = None
    ) -> None:
        """Store multiple entity types at once. types = {TYPE_NAME: description}."""
        props = properties or {}
        for type_name, description in types.items():
            props_json = json.dumps(props.get(type_name, {}), ensure_ascii=False)
            self._conn.execute(
                "INSERT OR REPLACE INTO entity_types (version_id, type_name, description, properties) "
                "VALUES (?, ?, ?, ?)",
                (version_id, type_name, description, props_json),
            )
        self._conn.commit()

    def get_entity_types(self, version_id: int | None = None) -> dict[str, str]:
        """Get entity types for a version (default: active). Returns {TYPE_NAME: description}."""
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return {}
        rows = self._conn.execute(
            "SELECT type_name, description FROM entity_types WHERE version_id = ? ORDER BY type_name",
            (vid,),
        ).fetchall()
        return {r["type_name"]: r["description"] for r in rows}

    def get_entity_type_properties(self, version_id: int | None = None) -> dict[str, dict]:
        """Get entity type properties for a version. Returns {TYPE_NAME: {prop_schema}}."""
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return {}
        rows = self._conn.execute(
            "SELECT type_name, properties FROM entity_types WHERE version_id = ?",
            (vid,),
        ).fetchall()
        return {r["type_name"]: json.loads(r["properties"]) for r in rows}

    def delete_entity_types(self, type_names: list[str], version_id: int | None = None) -> int:
        """Delete entity types by name from the active (or specified) version."""
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return 0
        count = 0
        for name in type_names:
            cur = self._conn.execute(
                "DELETE FROM entity_types WHERE version_id = ? AND type_name = ?",
                (vid, name),
            )
            count += cur.rowcount
        self._conn.commit()
        logger.info("Deleted %d entity types from version %d", count, vid)
        return count

    # ── Relationship Types ────────────────────────────────────

    def store_relationship_type(
        self, version_id: int, type_name: str, description: str, properties: dict | None = None
    ) -> None:
        props_json = json.dumps(properties or {}, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO relationship_types (version_id, type_name, description, properties) "
            "VALUES (?, ?, ?, ?)",
            (version_id, type_name, description, props_json),
        )
        self._conn.commit()

    def store_relationship_types_batch(
        self, version_id: int, types: dict[str, str], properties: dict[str, dict] | None = None
    ) -> None:
        props = properties or {}
        for type_name, description in types.items():
            props_json = json.dumps(props.get(type_name, {}), ensure_ascii=False)
            self._conn.execute(
                "INSERT OR REPLACE INTO relationship_types (version_id, type_name, description, properties) "
                "VALUES (?, ?, ?, ?)",
                (version_id, type_name, description, props_json),
            )
        self._conn.commit()

    def get_relationship_types(self, version_id: int | None = None) -> dict[str, str]:
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return {}
        rows = self._conn.execute(
            "SELECT type_name, description FROM relationship_types WHERE version_id = ? ORDER BY type_name",
            (vid,),
        ).fetchall()
        return {r["type_name"]: r["description"] for r in rows}

    def store_contradiction_pairs(self, version_id: int, pairs: list[list[str]]) -> None:
        """Store contradictory relationship pairs in the properties column of each type."""
        for pair in pairs:
            if len(pair) != 2:
                continue
            a, b = pair[0], pair[1]
            for this, other in [(a, b), (b, a)]:
                row = self._conn.execute(
                    "SELECT properties FROM relationship_types WHERE version_id = ? AND type_name = ?",
                    (version_id, this),
                ).fetchone()
                if row is None:
                    continue
                try:
                    props = json.loads(row["properties"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    props = {}
                props["contradicts"] = other
                self._conn.execute(
                    "UPDATE relationship_types SET properties = ? WHERE version_id = ? AND type_name = ?",
                    (json.dumps(props, ensure_ascii=False), version_id, this),
                )
        self._conn.commit()

    def get_contradiction_pairs(self, version_id: int | None = None) -> list[list[str]]:
        """Return unique contradictory pairs [[A, B], ...] stored in relationship_types properties."""
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return []
        rows = self._conn.execute(
            "SELECT type_name, properties FROM relationship_types WHERE version_id = ?",
            (vid,),
        ).fetchall()
        seen: set[frozenset] = set()
        pairs: list[list[str]] = []
        for row in rows:
            try:
                props = json.loads(row["properties"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            other = props.get("contradicts")
            if not other:
                continue
            key = frozenset([row["type_name"], other])
            if key not in seen:
                seen.add(key)
                pairs.append([row["type_name"], other])
        return pairs

    # ── Prompts ───────────────────────────────────────────────

    def store_prompt(
        self, version_id: int, prompt_key: str, prompt_text: str, description: str = ""
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO prompts (version_id, prompt_key, prompt_text, description) "
            "VALUES (?, ?, ?, ?)",
            (version_id, prompt_key, prompt_text, description),
        )
        self._conn.commit()

    def store_prompts_batch(self, version_id: int, prompts: dict[str, str]) -> None:
        """Store multiple prompts. prompts = {key: text}."""
        for key, text in prompts.items():
            self._conn.execute(
                "INSERT OR REPLACE INTO prompts (version_id, prompt_key, prompt_text) "
                "VALUES (?, ?, ?)",
                (version_id, key, text),
            )
        self._conn.commit()

    def get_prompt(self, prompt_key: str, version_id: int | None = None) -> str | None:
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return None
        row = self._conn.execute(
            "SELECT prompt_text FROM prompts WHERE version_id = ? AND prompt_key = ?",
            (vid, prompt_key),
        ).fetchone()
        return row["prompt_text"] if row else None

    def get_all_prompts(self, version_id: int | None = None) -> dict[str, str]:
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return {}
        rows = self._conn.execute(
            "SELECT prompt_key, prompt_text FROM prompts WHERE version_id = ? ORDER BY prompt_key",
            (vid,),
        ).fetchall()
        return {r["prompt_key"]: r["prompt_text"] for r in rows}

    # ── Bootstrap Sources ─────────────────────────────────────

    def record_bootstrap_source(
        self,
        version_id: int,
        source_type: str,
        source_path: str,
        page_count: int = 0,
        sample_text: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO bootstrap_sources (version_id, source_type, source_path, page_count, sample_text, processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (version_id, source_type, source_path, page_count, sample_text, now),
        )
        self._conn.commit()

    def get_bootstrap_sources(self, version_id: int | None = None) -> list[dict]:
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return []
        rows = self._conn.execute(
            "SELECT source_type, source_path, page_count, processed_at "
            "FROM bootstrap_sources WHERE version_id = ? ORDER BY processed_at",
            (vid,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Chat Sessions ────────────────────────────────────────

    def create_session(
        self, session_id: str, domain: str = "", name: str = ""
    ) -> str:
        """Create a new chat session. Returns the session_id."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO chat_sessions (session_id, name, started_at, domain) "
            "VALUES (?, ?, ?, ?)",
            (session_id, name, now, domain),
        )
        self._conn.commit()
        logger.info("Created chat session %s (name=%r)", session_id, name)
        return session_id

    def rename_session(self, session_id: str, name: str) -> None:
        """Set or update the display name of a session."""
        self._conn.execute(
            "UPDATE chat_sessions SET name = ? WHERE session_id = ?",
            (name, session_id),
        )
        self._conn.commit()

    def update_session_summary(
        self, session_id: str, summary: str, compacted_turns: int
    ) -> None:
        """Store or update the compacted summary for a session."""
        self._conn.execute(
            "UPDATE chat_sessions SET summary = ?, compacted_turns = ? "
            "WHERE session_id = ?",
            (summary, compacted_turns, session_id),
        )
        self._conn.commit()

    def get_last_session(self) -> dict[str, Any] | None:
        """Return the most recent chat session, or None."""
        row = self._conn.execute(
            "SELECT * FROM chat_sessions ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_session_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a session by display name, session_id, or session_id prefix."""
        # Exact name match
        row = self._conn.execute(
            "SELECT * FROM chat_sessions WHERE name = ? ORDER BY started_at DESC LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return dict(row)
        # Exact session_id match
        row = self._conn.execute(
            "SELECT * FROM chat_sessions WHERE session_id = ? LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return dict(row)
        # Prefix match on session_id (e.g. "e77bdaab" matches "e77bdaab-5a4c-...")
        row = self._conn.execute(
            "SELECT * FROM chat_sessions WHERE session_id LIKE ? ORDER BY started_at DESC LIMIT 1",
            (name + "%",),
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent sessions that have at least one episode, newest first."""
        rows = self._conn.execute(
            "SELECT cs.*, COUNT(re.id) AS episode_count "
            "FROM chat_sessions cs "
            "JOIN reasoning_episodes re ON re.session_id = cs.session_id "
            "GROUP BY cs.session_id "
            "HAVING COUNT(re.id) > 0 "
            "ORDER BY cs.started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_episodes(
        self, session_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Retrieve reasoning episodes for a specific session, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM reasoning_episodes WHERE session_id = ? "
            "ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Reasoning Episodes ────────────────────────────────────

    def store_reasoning_episode(
        self,
        question: str,
        answer: str,
        steps_taken: int = 0,
        empty_results: int = 0,
        timed_out: bool = False,
        max_steps_reached: bool = False,
        doom_loop_triggered: bool = False,
        elapsed_seconds: float = 0.0,
        section_ids: list[str] | None = None,
        actions_log: list[dict[str, str]] | None = None,
        confidence: float = 0.0,
        groundedness: float = 0.0,
        completeness: float = 0.0,
        assessment_reasoning: str = "",
        assessment_gaps: list[str] | None = None,
        entities_added: int = 0,
        rels_added: int = 0,
        session_id: str | None = None,
    ) -> int:
        """Store a complete reasoning episode for later analysis."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO reasoning_episodes "
            "(session_id, question, answer, steps_taken, empty_results, timed_out, max_steps_reached, "
            "doom_loop_triggered, elapsed_seconds, section_ids, actions_log, "
            "confidence, groundedness, completeness, assessment_reasoning, assessment_gaps, "
            "entities_added, rels_added, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                question,
                answer,
                steps_taken,
                empty_results,
                int(timed_out),
                int(max_steps_reached),
                int(doom_loop_triggered),
                elapsed_seconds,
                json.dumps(section_ids or []),
                json.dumps(actions_log or []),
                confidence,
                groundedness,
                completeness,
                assessment_reasoning,
                json.dumps(assessment_gaps or []),
                entities_added,
                rels_added,
                now,
            ),
        )
        self._conn.commit()
        episode_id = cur.lastrowid
        assert episode_id is not None
        logger.info("Stored reasoning episode %d for question: %.60s...", episode_id, question)
        return episode_id

    def get_reasoning_episodes(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Retrieve recent reasoning episodes, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM reasoning_episodes ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_reasoning_stats(self) -> dict[str, Any]:
        """Aggregate statistics across all reasoning episodes."""
        row = self._conn.execute(
            "SELECT "
            "  COUNT(*) AS total_episodes, "
            "  AVG(steps_taken) AS avg_steps, "
            "  AVG(elapsed_seconds) AS avg_elapsed, "
            "  AVG(confidence) AS avg_confidence, "
            "  AVG(groundedness) AS avg_groundedness, "
            "  AVG(completeness) AS avg_completeness, "
            "  SUM(entities_added) AS total_entities_added, "
            "  SUM(rels_added) AS total_rels_added, "
            "  SUM(timed_out) AS total_timeouts, "
            "  SUM(doom_loop_triggered) AS total_doom_loops "
            "FROM reasoning_episodes"
        ).fetchone()
        if not row:
            return {}
        return dict(row)

    # ── Activity Log ──────────────────────────────────────────

    def log_activity(
        self,
        action_type: str,
        action_id: str,
        action_label: str,
        item_type: str,
        item_name: str,
        item_detail: str = "",
    ) -> None:
        """Log a single item added/changed by an action."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO activity_log (action_type, action_id, action_label, "
            "item_type, item_name, item_detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (action_type, action_id, action_label, item_type, item_name, item_detail, now),
        )

    def log_activity_batch(
        self,
        action_type: str,
        action_id: str,
        action_label: str,
        items: list[tuple[str, str, str]],
    ) -> None:
        """Log multiple items for one action. items = [(item_type, item_name, item_detail), ...]"""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            "INSERT INTO activity_log (action_type, action_id, action_label, "
            "item_type, item_name, item_detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(action_type, action_id, action_label, t, n, d, now) for t, n, d in items],
        )
        self._conn.commit()

    def list_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List distinct actions with item counts, newest first."""
        rows = self._conn.execute(
            "SELECT action_type, action_id, action_label, "
            "  MIN(created_at) AS started_at, "
            "  COUNT(*) AS item_count, "
            "  SUM(CASE WHEN item_type = 'entity' THEN 1 ELSE 0 END) AS entities, "
            "  SUM(CASE WHEN item_type = 'relationship' THEN 1 ELSE 0 END) AS relationships, "
            "  SUM(CASE WHEN item_type = 'entity_type' THEN 1 ELSE 0 END) AS entity_types, "
            "  SUM(CASE WHEN item_type = 'relationship_type' THEN 1 ELSE 0 END) AS rel_types, "
            "  SUM(CASE WHEN item_type = 'prompt' THEN 1 ELSE 0 END) AS prompts "
            "FROM activity_log "
            "GROUP BY action_type, action_id "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_action_items(self, action_type: str, action_id: str) -> list[dict[str, Any]]:
        """Get all items logged for a specific action."""
        rows = self._conn.execute(
            "SELECT * FROM activity_log WHERE action_type = ? AND action_id = ? ORDER BY id",
            (action_type, action_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Extracted Tables (SQL store for tabular data) ──────────

    def store_table(
        self,
        source_doc: str,
        source_section: str,
        table_name: str,
        columns: list[str],
        rows: list[dict[str, str]],
    ) -> int:
        """Store a parsed table with typed rows. Returns the table_id."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO extracted_tables (source_doc, source_section, table_name, columns_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_doc, source_section, table_name, json.dumps(columns, ensure_ascii=False), now),
        )
        table_id = cur.lastrowid
        assert table_id is not None
        for idx, row in enumerate(rows):
            self._conn.execute(
                "INSERT INTO extracted_table_rows (table_id, row_index, row_json) VALUES (?, ?, ?)",
                (table_id, idx, json.dumps(row, ensure_ascii=False)),
            )
        self._conn.commit()
        logger.info("Stored table '%s' with %d rows (table_id=%d)", table_name, len(rows), table_id)
        return table_id

    def query_table(
        self,
        table_name: str,
        filters: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query rows from an extracted table, optionally filtering by column values."""
        row = self._conn.execute(
            "SELECT id, columns_json FROM extracted_tables WHERE table_name = ? ORDER BY id DESC LIMIT 1",
            (table_name,),
        ).fetchone()
        if not row:
            return []
        table_id = row["id"]
        columns = json.loads(row["columns_json"])

        rows = self._conn.execute(
            "SELECT row_json FROM extracted_table_rows WHERE table_id = ? ORDER BY row_index LIMIT ?",
            (table_id, limit),
        ).fetchall()

        results = []
        for r in rows:
            row_data = json.loads(r["row_json"])
            if filters:
                match = all(
                    str(row_data.get(k, "")).lower() == str(v).lower()
                    for k, v in filters.items()
                )
                if not match:
                    continue
            results.append(row_data)
        return results

    def query_table_sql(self, table_name: str, where_clause: str = "", limit: int = 100) -> list[dict]:
        """Query table rows with a flexible text-based filter on JSON fields.

        *where_clause* is matched against row_json via LIKE for simple cases.
        For structured queries, use query_table() with dict filters.
        """
        row = self._conn.execute(
            "SELECT id FROM extracted_tables WHERE table_name = ? ORDER BY id DESC LIMIT 1",
            (table_name,),
        ).fetchone()
        if not row:
            return []
        table_id = row["id"]

        if where_clause:
            rows = self._conn.execute(
                "SELECT row_json FROM extracted_table_rows WHERE table_id = ? AND row_json LIKE ? ORDER BY row_index LIMIT ?",
                (table_id, f"%{where_clause}%", limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT row_json FROM extracted_table_rows WHERE table_id = ? ORDER BY row_index LIMIT ?",
                (table_id, limit),
            ).fetchall()
        return [json.loads(r["row_json"]) for r in rows]

    def list_tables(self) -> list[dict[str, Any]]:
        """List all extracted tables with row counts."""
        rows = self._conn.execute(
            "SELECT t.id, t.source_doc, t.source_section, t.table_name, t.columns_json, t.created_at, "
            "  (SELECT COUNT(*) FROM extracted_table_rows r WHERE r.table_id = t.id) AS row_count "
            "FROM extracted_tables t ORDER BY t.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_tables_for_doc(self, source_doc: str) -> int:
        """Delete all extracted tables for a document (for re-ingest)."""
        table_ids = self._conn.execute(
            "SELECT id FROM extracted_tables WHERE source_doc = ?", (source_doc,)
        ).fetchall()
        count = 0
        for tid in table_ids:
            self._conn.execute("DELETE FROM extracted_table_rows WHERE table_id = ?", (tid["id"],))
            count += 1
        self._conn.execute("DELETE FROM extracted_tables WHERE source_doc = ?", (source_doc,))
        self._conn.commit()
        return count

    # ── Export / Import ───────────────────────────────────────

    def export_version(self, version_id: int | None = None) -> dict[str, Any]:
        """Export an entire ontology version as a JSON-serializable dict."""
        vid = version_id or self.get_active_version_id()
        if vid is None:
            return {}

        version_row = self._conn.execute(
            "SELECT * FROM ontology_versions WHERE version_id = ?", (vid,)
        ).fetchone()
        if not version_row:
            return {}

        return {
            "version": dict(version_row),
            "entity_types": self.get_entity_types(vid),
            "relationship_types": self.get_relationship_types(vid),
            "prompts": self.get_all_prompts(vid),
            "sources": self.get_bootstrap_sources(vid),
        }

    def import_version(self, data: dict[str, Any], activate: bool = True) -> int:
        """Import an ontology version from a dict (e.g., loaded from JSON backup)."""
        version_info = data.get("version", {})
        vid = self.create_ontology_version(
            name=version_info.get("name", "imported"),
            description=version_info.get("description", ""),
            domain=version_info.get("domain", ""),
            activate=activate,
        )
        self.store_entity_types_batch(vid, data.get("entity_types", {}))
        self.store_relationship_types_batch(vid, data.get("relationship_types", {}))
        self.store_prompts_batch(vid, data.get("prompts", {}))
        return vid
