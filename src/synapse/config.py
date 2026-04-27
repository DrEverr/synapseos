"""SynapseOS configuration — Settings + SQLite-backed OntologyRegistry.

The OntologyRegistry reads entity/relationship types from the InstanceStore (SQLite).
Before bootstrap, it falls back to a minimal base ontology from YAML.
After bootstrap, everything comes from the generated ontology in SQLite.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class Settings(BaseSettings):
    """Global SynapseOS settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SYNAPSE_",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM — default model used when per-phase model is not set
    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openrouter/auto"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8192
    llm_timeout: float = 180

    # Per-phase model overrides (fall back to llm_model if empty)
    bootstrap_model: str = ""
    extraction_model: str = ""
    chat_model: str = ""
    challenger_model: str = ""
    compaction_model: str = "google/gemini-2.0-flash-001"

    # FalkorDB
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: str = ""
    graph_name: str = "synapse_kg"

    # Instance directory (stores SQLite DB, text cache, etc.)
    # Default: ~/.synapse/dbs/<graph_name>/
    instance_dir: str = ""

    # Extraction
    ontology: str = "base"
    max_concurrency: int = 4
    section_timeout: float = 600
    structure_max_pages_per_node: int = 10
    structure_max_tokens_per_node: int = 20000
    toc_scan_pages: int = 10
    toc_accuracy_threshold: float = 0.60


    # Entity Resolution
    fuzzy_match_threshold: float = 0.90

    # Chat
    max_reasoning_steps: int = 20
    doom_loop_threshold: int = 5
    reasoning_timeout: float = 300
    reasoning_step_max_tokens: int = 2048
    chat_context_max_tokens: int = 4000
    compaction_threshold_turns: int = 4
    debate_enabled: bool = False
    debate_max_rounds: int = 2
    debate_confidence_threshold: float = 0.7

    # Bootstrap
    bootstrap_sample_pages: int = 30
    bootstrap_max_entity_types: int = 35
    bootstrap_max_rel_types: int = 50

    # Confidence decay
    confidence_decay_rate: float = 0.99

    # Logging
    log_level: str = "INFO"

    def get_instance_dir(self) -> Path:
        """Resolve instance directory, creating it if needed."""
        if self.instance_dir:
            p = Path(self.instance_dir)
        else:
            p = Path.home() / ".synapse" / "dbs" / self.graph_name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_instance_store(self) -> InstanceStore:
        """Get the SQLite instance store for this instance."""
        db_path = self.get_instance_dir() / "instance.db"
        return InstanceStore(db_path)

    def get_text_cache_dir(self) -> Path:
        return self.get_instance_dir() / "text_cache"


class OntologyRegistry:
    """Loads entity and relationship types from the InstanceStore (SQLite).

    Falls back to YAML base ontology if no active version exists in the store
    (i.e., before bootstrap has been run).
    """

    def __init__(self, store: InstanceStore | None = None, ontology_name: str = "base") -> None:
        self.entity_types: dict[str, str] = {}
        self.relationship_types: dict[str, str] = {}
        self._store = store

        if store and store.get_active_version_id() is not None:
            # Load from SQLite (post-bootstrap)
            self.entity_types = store.get_entity_types()
            self.relationship_types = store.get_relationship_types()
            logger.info(
                "Loaded ontology from instance store: %d entity types, %d relationship types",
                len(self.entity_types),
                len(self.relationship_types),
            )
        else:
            # Fallback: load from YAML (pre-bootstrap or no store)
            self._load_yaml("base")
            if ontology_name != "base":
                self._load_yaml(ontology_name)

    def _load_yaml(self, name: str) -> None:
        """Load entity/relationship types from a YAML file in config/ontologies/."""
        path = CONFIG_DIR / "ontologies" / f"{name}.yaml"
        if not path.exists():
            logger.warning("Ontology YAML not found: %s", path)
            return
        with open(path) as f:
            data = yaml.safe_load(f)
        if "entity_types" in data:
            self.entity_types.update(data["entity_types"])
        if "relationship_types" in data:
            self.relationship_types.update(data["relationship_types"])
        logger.info(
            "Loaded ontology YAML '%s': %d entity types, %d relationship types",
            name,
            len(data.get("entity_types", {})),
            len(data.get("relationship_types", {})),
        )

    def format_entity_types(self) -> str:
        """Format entity types for use in LLM prompts."""
        return "\n".join(f"- {etype}: {desc}" for etype, desc in sorted(self.entity_types.items()))

    def format_relationship_types(self) -> str:
        """Format relationship types for use in LLM prompts."""
        return "\n".join(
            f"- {rtype}: {desc}" for rtype, desc in sorted(self.relationship_types.items())
        )

    def get_contradiction_pairs(self) -> list[list[str]]:
        """Return contradictory relationship pairs [[A, B], ...] from the store."""
        if self._store:
            return self._store.get_contradiction_pairs()
        return []


# ── Singleton ─────────────────────────────────────────────

_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
