"""Tests for the SQLite-backed InstanceStore."""

import json
import tempfile
from pathlib import Path

import pytest

from synapse.storage.instance_store import InstanceStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_instance.db"
    s = InstanceStore(db_path)
    yield s
    s.close()


def test_metadata(store):
    assert store.get_meta("nonexistent") == ""
    assert store.get_meta("nonexistent", "default") == "default"
    store.set_meta("key1", "value1")
    assert store.get_meta("key1") == "value1"


def test_bootstrap_flag(store):
    assert not store.is_bootstrapped()
    store.mark_bootstrapped("cooking")
    assert store.is_bootstrapped()
    assert store.get_meta("domain") == "cooking"


def test_ontology_version_lifecycle(store):
    vid = store.create_ontology_version("v1", "test version", "cooking", activate=True)
    assert vid is not None
    assert store.get_active_version_id() == vid

    vid2 = store.create_ontology_version("v2", "second version", "cooking", activate=True)
    assert store.get_active_version_id() == vid2

    store.activate_version(vid)
    assert store.get_active_version_id() == vid


def test_entity_types(store):
    vid = store.create_ontology_version("v1")
    store.store_entity_types_batch(
        vid,
        {
            "INGREDIENT": "A food ingredient",
            "RECIPE": "A cooking recipe",
        },
    )
    types = store.get_entity_types(vid)
    assert len(types) == 2
    assert "INGREDIENT" in types
    assert types["INGREDIENT"] == "A food ingredient"


def test_relationship_types(store):
    vid = store.create_ontology_version("v1")
    store.store_relationship_types_batch(
        vid,
        {
            "HAS_INGREDIENT": "Recipe has an ingredient",
            "PAIRS_WITH": "Ingredients that go well together",
        },
    )
    types = store.get_relationship_types(vid)
    assert len(types) == 2
    assert "HAS_INGREDIENT" in types


def test_prompts(store):
    vid = store.create_ontology_version("v1")
    store.store_prompt(vid, "reasoning_system", "You are an agent...")
    store.store_prompt(vid, "reasoning_user", "Question: {question}")

    assert store.get_prompt("reasoning_system", vid) == "You are an agent..."
    assert store.get_prompt("nonexistent", vid) is None

    all_prompts = store.get_all_prompts(vid)
    assert len(all_prompts) == 2


def test_prompts_batch(store):
    vid = store.create_ontology_version("v1")
    store.store_prompts_batch(
        vid,
        {
            "entity_extraction_system": "Extract entities...",
            "relationship_extraction_system": "Extract relationships...",
            "reasoning_system": "You are an agent...",
        },
    )
    all_prompts = store.get_all_prompts(vid)
    assert len(all_prompts) == 3


def test_bootstrap_sources(store):
    vid = store.create_ontology_version("v1")
    store.record_bootstrap_source(vid, "pdf", "/path/to/doc.pdf", page_count=42)
    sources = store.get_bootstrap_sources(vid)
    assert len(sources) == 1
    assert sources[0]["source_path"] == "/path/to/doc.pdf"
    assert sources[0]["page_count"] == 42


def test_export_import(store):
    vid = store.create_ontology_version("v1", "test", "cooking")
    store.store_entity_types_batch(vid, {"INGREDIENT": "food item", "RECIPE": "a recipe"})
    store.store_relationship_types_batch(vid, {"HAS_INGREDIENT": "recipe has ingredient"})
    store.store_prompts_batch(vid, {"reasoning_system": "You are an agent..."})

    exported = store.export_version(vid)
    assert exported["entity_types"]["INGREDIENT"] == "food item"
    assert "reasoning_system" in exported["prompts"]

    vid2 = store.import_version(exported)
    assert store.get_active_version_id() == vid2
    types = store.get_entity_types(vid2)
    assert "INGREDIENT" in types


def test_version_isolation(store):
    vid1 = store.create_ontology_version("v1", activate=False)
    vid2 = store.create_ontology_version("v2", activate=True)
    store.store_entity_types_batch(vid1, {"TYPE_A": "from v1"})
    store.store_entity_types_batch(vid2, {"TYPE_B": "from v2"})

    assert "TYPE_A" in store.get_entity_types(vid1)
    assert "TYPE_A" not in store.get_entity_types(vid2)
    assert "TYPE_B" in store.get_entity_types(vid2)
    assert "TYPE_B" not in store.get_entity_types(vid1)


def test_list_versions(store):
    store.create_ontology_version("v1", domain="cooking")
    store.create_ontology_version("v2", domain="chemistry")
    versions = store.list_versions()
    assert len(versions) == 2
    assert versions[0]["name"] == "v1"
    assert versions[1]["domain"] == "chemistry"
