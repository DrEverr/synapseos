"""Tests for entity resolution and deduplication."""

from synapse.models.entity import Entity
from synapse.resolution.linker import are_same_entity, resolve_entities


def test_exact_match():
    assert are_same_entity("hello", "hello")


def test_case_insensitive():
    assert are_same_entity("Hello", "hello")


def test_trademark_variants():
    assert are_same_entity("SILRES® BS 5137", "SILRES BS 5137")


def test_different_types_not_same():
    assert not are_same_entity("water", "water", "INGREDIENT", "PROCESS")


def test_fuzzy_match():
    assert are_same_entity("polydimethylsiloxane", "polydimethyl siloxane", threshold=0.85)


def test_prefix_match():
    assert are_same_entity("silicone resin", "silicone resin emulsion")


def test_no_match():
    assert not are_same_entity("apple", "banana")


def test_resolve_dedup_exact():
    entities = [
        Entity(text="Apple", entity_type="INGREDIENT", confidence=0.9),
        Entity(text="apple", entity_type="INGREDIENT", confidence=0.8),
    ]
    result = resolve_entities(entities)
    assert len(result) == 1
    assert result[0].confidence == 0.9


def test_resolve_different_types_preserved():
    entities = [
        Entity(text="water", entity_type="INGREDIENT", confidence=0.9),
        Entity(text="water", entity_type="PROCESS", confidence=0.8),
    ]
    result = resolve_entities(entities)
    assert len(result) == 2


def test_resolve_empty():
    assert resolve_entities([]) == []


def test_resolve_single():
    entities = [Entity(text="Salt", entity_type="INGREDIENT", confidence=0.9)]
    result = resolve_entities(entities)
    assert len(result) == 1
