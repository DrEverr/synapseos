"""Dependency injection for the FastAPI web app."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from synapse.config import OntologyRegistry, Settings, get_settings
from synapse.llm.client import LLMClient
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

logger = logging.getLogger(__name__)

security = HTTPBasic()

# Shared singletons — initialized at startup
_settings: Settings | None = None
_graph: GraphStore | None = None
_store: InstanceStore | None = None
_text_cache: TextCache | None = None
_ontology: OntologyRegistry | None = None
_store_lock = asyncio.Lock()


def init_dependencies() -> None:
    """Initialize all shared dependencies. Called once at app startup."""
    global _settings, _graph, _store, _text_cache, _ontology

    import os
    _settings = get_settings()
    # Force graph name for the demo — defaults to "wacker-3" locally,
    # "wacker" in Docker (set via fly.toml env)
    _settings.graph_name = os.environ.get("SYNAPSE_GRAPH_NAME", "wacker-3")

    _store = _settings.get_instance_store()
    # Allow SQLite access from FastAPI thread pool
    _store._conn.close()
    import sqlite3
    _store._conn = sqlite3.connect(str(_store._path), check_same_thread=False)
    _store._conn.row_factory = sqlite3.Row
    _store._conn.execute("PRAGMA journal_mode=WAL")
    _text_cache = TextCache(cache_dir=_settings.get_text_cache_dir())
    _ontology = OntologyRegistry(store=_store, ontology_name=_settings.ontology)

    try:
        _graph = GraphStore(
            host=_settings.falkordb_host,
            port=_settings.falkordb_port,
            password=_settings.falkordb_password,
            graph_name=_settings.graph_name,
        )
        logger.info("Connected to FalkorDB graph: %s", _settings.graph_name)
    except Exception as e:
        logger.error("Failed to connect to FalkorDB: %s", e)
        _graph = None


def close_dependencies() -> None:
    """Cleanup at shutdown."""
    global _store
    if _store:
        _store.close()
        _store = None


def get_settings_dep() -> Settings:
    assert _settings is not None
    return _settings


def get_graph() -> GraphStore:
    if _graph is None:
        raise HTTPException(status_code=503, detail="FalkorDB not available")
    return _graph


def get_store() -> InstanceStore:
    assert _store is not None
    return _store


def get_store_lock() -> asyncio.Lock:
    return _store_lock


def get_text_cache() -> TextCache:
    assert _text_cache is not None
    return _text_cache


def get_ontology() -> OntologyRegistry:
    assert _ontology is not None
    return _ontology


def get_llm() -> LLMClient:
    s = get_settings_dep()
    chat_model = s.chat_model or s.llm_model
    return LLMClient(
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        model=chat_model,
        timeout=s.llm_timeout,
    )


def verify_credentials(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
) -> str:
    """HTTP Basic Auth check. Returns username on success."""
    import os

    expected_user = os.environ.get("DEMO_USERNAME", "demo")
    expected_pass = os.environ.get("DEMO_PASSWORD", "wacker2026")

    user_ok = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), expected_pass.encode())

    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
