"""Ontology discovery — detect entity/relationship types missing from the current ontology.

During ingestion, samples the document text and asks the LLM whether the current
ontology is sufficient or whether new types should be added.  Types with confidence
>= the threshold (default 0.85) are added automatically; those below the threshold
are logged at DEBUG level so the user can review them in the log file.
"""

from __future__ import annotations

import logging
from typing import Any

from synapse.config import OntologyRegistry
from synapse.llm.client import LLMClient
from synapse.models.document import Document
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85

_DISCOVERY_SYSTEM = (
    "You are an ontology analyst. You compare document content against an existing "
    "knowledge-graph ontology and identify entity types and relationship types that "
    "are clearly present in the document but MISSING from the ontology."
)

_DISCOVERY_USER = """Below is a sample of a document being ingested into a knowledge graph.

DOCUMENT: "{document_title}"

CURRENT ENTITY TYPES:
{entity_types}

CURRENT RELATIONSHIP TYPES:
{relationship_types}

SAMPLE TEXT:
{sample_text}

Analyze the sample text and determine if there are entity types or relationship types
that are clearly present in this document but NOT covered by the current ontology.

Rules:
- Only propose types that are genuinely NEW — not synonyms or subsets of existing types.
- Each proposed type must have a clear, distinct semantic role in this domain.
- Assign a confidence score (0.0-1.0) reflecting how certain you are that this type
  is important and distinct enough to warrant a dedicated ontology entry.
- Use UPPER_SNAKE_CASE for type names.
- Be conservative — only propose types you are highly confident about.

Return a JSON object:
{{
  "entity_types": [
    {{"type_name": "EXAMPLE_TYPE", "description": "What this type represents", "confidence": 0.92, "evidence": "Brief quote or reasoning from the text"}}
  ],
  "relationship_types": [
    {{"type_name": "EXAMPLE_REL", "description": "What this relationship represents", "confidence": 0.88, "evidence": "Brief quote or reasoning from the text"}}
  ]
}}

If the current ontology is sufficient, return empty arrays.
Return ONLY the JSON object."""


def _build_sample_text(doc: Document, max_chars: int = 12000) -> str:
    """Build a representative text sample from the document for ontology analysis."""
    leaves = doc.leaf_sections()
    if not leaves:
        return ""

    # Take text from evenly spaced sections to get broad coverage
    if len(leaves) <= 4:
        selected = leaves
    else:
        step = max(1, len(leaves) // 4)
        selected = [leaves[i] for i in range(0, len(leaves), step)][:5]

    parts: list[str] = []
    budget = max_chars
    for section in selected:
        text = section.text.strip()
        if not text:
            continue
        if len(text) > budget:
            text = text[:budget]
        parts.append(f"--- Section: {section.title} ---\n{text}")
        budget -= len(text)
        if budget <= 0:
            break

    return "\n\n".join(parts)


async def discover_ontology_gaps(
    doc: Document,
    llm: LLMClient,
    ontology: OntologyRegistry,
    store: InstanceStore | None = None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[dict[str, str], dict[str, str]]:
    """Scan a document for entity/relationship types missing from the ontology.

    Returns (new_entity_types, new_relationship_types) that were auto-added
    (confidence >= threshold).  Below-threshold candidates are logged at DEBUG.
    """
    sample = _build_sample_text(doc)
    if not sample:
        logger.debug("No text available for ontology discovery, skipping")
        return {}, {}

    user_prompt = _DISCOVERY_USER.format(
        document_title=doc.title,
        entity_types=ontology.format_entity_types(),
        relationship_types=ontology.format_relationship_types(),
        sample_text=sample,
    )

    try:
        result = await llm.complete_json_lenient(
            system=_DISCOVERY_SYSTEM,
            user=user_prompt,
            max_tokens=2048,
        )
    except Exception:
        logger.warning("Ontology discovery LLM call failed, skipping")
        return {}, {}

    if not isinstance(result, dict):
        return {}, {}

    added_entities: dict[str, str] = {}
    added_rels: dict[str, str] = {}

    version_id = store.get_active_version_id() if store else None

    # --- Process entity type candidates ---
    for item in result.get("entity_types", []):
        if not isinstance(item, dict):
            continue
        _process_candidate(
            item=item,
            kind="entity type",
            existing=ontology.entity_types,
            added=added_entities,
            confidence_threshold=confidence_threshold,
            store=store,
            version_id=version_id,
            store_fn=_store_entity_type,
            registry_dict=ontology.entity_types,
        )

    # --- Process relationship type candidates ---
    for item in result.get("relationship_types", []):
        if not isinstance(item, dict):
            continue
        _process_candidate(
            item=item,
            kind="relationship type",
            existing=ontology.relationship_types,
            added=added_rels,
            confidence_threshold=confidence_threshold,
            store=store,
            version_id=version_id,
            store_fn=_store_relationship_type,
            registry_dict=ontology.relationship_types,
        )

    return added_entities, added_rels


def _process_candidate(
    *,
    item: dict[str, Any],
    kind: str,
    existing: dict[str, str],
    added: dict[str, str],
    confidence_threshold: float,
    store: InstanceStore | None,
    version_id: int | None,
    store_fn: Any,
    registry_dict: dict[str, str],
) -> None:
    """Evaluate a single candidate type and either auto-add or log it."""
    type_name = item.get("type_name", "").upper().strip()
    description = item.get("description", "").strip()
    confidence = float(item.get("confidence", 0.0))
    evidence = item.get("evidence", "")

    if not type_name or not description:
        return

    if type_name in existing:
        return  # already in ontology

    if confidence >= confidence_threshold:
        # Auto-add
        if store and version_id:
            store_fn(store, version_id, type_name, description)
        registry_dict[type_name] = description
        added[type_name] = description
        logger.warning(
            "ONTOLOGY UPDATED: Added %s %s (confidence=%.0f%%): %s",
            kind,
            type_name,
            confidence * 100,
            description,
        )
    else:
        # Below threshold — log for manual review
        logger.debug(
            "Ontology candidate below threshold (%.0f%%): %s %s — %s [evidence: %s]",
            confidence * 100,
            kind,
            type_name,
            description,
            evidence,
        )


def _store_entity_type(
    store: InstanceStore, version_id: int, type_name: str, description: str
) -> None:
    store.store_entity_type(version_id, type_name, description)


def _store_relationship_type(
    store: InstanceStore, version_id: int, type_name: str, description: str
) -> None:
    store.store_relationship_type(version_id, type_name, description)
