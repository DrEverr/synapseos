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
