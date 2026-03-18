"""Post-answer graph enrichment — extract new knowledge from AI answers and merge into the graph."""

from __future__ import annotations

import logging
from typing import Any

from synapse.chat.prompts import ENRICHMENT_SYSTEM, ENRICHMENT_USER
from synapse.config import OntologyRegistry
from synapse.llm.templates import safe_format
from synapse.llm.client import LLMClient
from synapse.models.entity import Entity
from synapse.models.relationship import Relationship
from synapse.resolution.linker import are_same_entity
from synapse.resolution.normalizer import normalize_entity_name
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)


def _fetch_existing_entity_names(graph: GraphStore) -> list[tuple[str, str]]:
    try:
        result = graph.query(
            "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
            "RETURN DISTINCT n.canonical_name, labels(n)[0]"
        )
        return [(row[0], row[1]) for row in result if row[0] and row[1]]
    except Exception as e:
        logger.warning("Failed to fetch existing entities: %s", e)
        return []


def _is_duplicate(
    name: str, entity_type: str, existing: list[tuple[str, str]], threshold: float = 0.90
) -> bool:
    canonical = normalize_entity_name(name)
    for existing_name, existing_type in existing:
        if are_same_entity(canonical, existing_name, entity_type, existing_type, threshold):
            return True
    return False


async def enrich_graph_from_answer(
    answer: str,
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    fuzzy_threshold: float = 0.90,
    store: InstanceStore | None = None,
) -> tuple[int, int]:
    """Extract entities and relationships from an AI answer and merge new ones into the graph."""
    existing = _fetch_existing_entity_names(graph)
    existing_formatted = "\n".join(f"- {name} ({etype})" for name, etype in sorted(existing)[:200])
    if not existing_formatted:
        existing_formatted = "(graph is empty)"

    entity_type_list = "\n".join(f"- {k}: {v}" for k, v in sorted(ontology.entity_types.items()))
    rel_type_list = "\n".join(f"- {k}: {v}" for k, v in sorted(ontology.relationship_types.items()))

    # Get prompts from store or fallback
    system = ENRICHMENT_SYSTEM
    user_template = ENRICHMENT_USER
    if store:
        custom_system = store.get_prompt("enrichment_system")
        custom_user = store.get_prompt("enrichment_user")
        if custom_system:
            system = custom_system
        if custom_user:
            user_template = custom_user

    user_prompt = safe_format(
        user_template,
        answer_text=answer,
        question=question,
        entity_types=entity_type_list,
        relationship_types=rel_type_list,
        existing_entities=existing_formatted,
    )

    try:
        data: Any = await llm.complete_json_lenient(
            system=system, user=user_prompt, temperature=0.0, max_tokens=4096
        )
    except Exception as e:
        logger.warning("Enrichment LLM call failed: %s", e)
        return 0, 0

    if not isinstance(data, dict):
        return 0, 0

    raw_entities = data.get("entities", [])
    raw_relationships = data.get("relationships", [])
    if not raw_entities and not raw_relationships:
        return 0, 0

    valid_entity_types = set(ontology.entity_types.keys())
    entities_added = 0
    entity_map: dict[str, Entity] = {}

    for raw in raw_entities:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text", "").strip()
        etype = raw.get("entity_type", "").strip().upper()
        confidence = float(raw.get("confidence", 0.7))
        if not text or not etype or etype not in valid_entity_types:
            continue

        if _is_duplicate(text, etype, existing, fuzzy_threshold):
            entity_map[text] = Entity(
                text=text,
                entity_type=etype,
                canonical_name=normalize_entity_name(text),
                confidence=confidence,
                source_doc="chat:enrichment",
            )
            continue

        entity = Entity(
            text=text,
            entity_type=etype,
            canonical_name=normalize_entity_name(text),
            confidence=confidence,
            source_doc="chat:enrichment",
            properties=raw.get("properties", {}),
        )
        entity_map[text] = entity
        try:
            graph.store_entity(entity)
            entities_added += 1
            existing.append((entity.canonical_name, entity.entity_type))
        except Exception as e:
            logger.warning("Enrichment: failed to store entity %s: %s", text, e)

    valid_rel_types = set(ontology.relationship_types.keys())
    rels_added = 0

    for raw in raw_relationships:
        if not isinstance(raw, dict):
            continue
        subject = raw.get("subject", "").strip()
        predicate = raw.get("predicate", "").strip().upper()
        obj = raw.get("object", "").strip()
        subj_type = raw.get("subject_type", "").strip().upper()
        obj_type = raw.get("object_type", "").strip().upper()
        confidence = float(raw.get("confidence", 0.7))
        if not subject or not predicate or not obj or predicate not in valid_rel_types:
            continue
        if not subj_type and subject in entity_map:
            subj_type = entity_map[subject].entity_type
        if not obj_type and obj in entity_map:
            obj_type = entity_map[obj].entity_type

        rel = Relationship(
            subject=subject,
            subject_type=subj_type,
            predicate=predicate,
            object=obj,
            object_type=obj_type,
            confidence=confidence,
            source_doc="chat:enrichment",
        )
        try:
            graph.store_relationship(rel)
            rels_added += 1
        except Exception as e:
            logger.warning("Enrichment: failed to store relationship: %s", e)

    logger.info("Enrichment: %d entities added, %d relationships added", entities_added, rels_added)
    return entities_added, rels_added
