"""LLM-based relationship extraction from document sections.

Uses prompts from the InstanceStore (generated during bootstrap).
"""

from __future__ import annotations

import logging

from synapse.config import OntologyRegistry
from synapse.llm.client import LLMClient
from synapse.llm.templates import safe_format
from synapse.models.document import Section
from synapse.models.entity import Entity
from synapse.models.relationship import Relationship
from synapse.resolution.normalizer import normalize_entity_name
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)


# ── Fallback prompts (pre-bootstrap) ─────────────────────

_FALLBACK_SYSTEM = "You are an expert relationship extraction system for technical documents."

_FALLBACK_USER = """Extract relationships between entities from the following text.

DOCUMENT CONTEXT:
- Document: "{document_title}"
- Section: "{section_title}"

SECTION TEXT:
"{section_text}"

ENTITIES FOUND IN THIS SECTION:
{entities}

RELATIONSHIP TYPES:
{relationship_types}

OUTPUT FORMAT:
Return a JSON array. Each relationship must have:
- "subject": exact text of the subject entity (must match an entity above)
- "predicate": one of the relationship types above (UPPERCASE)
- "object": exact text of the object entity (must match an entity above)
- "confidence": confidence score between 0.0 and 1.0

RULES:
1. subject and object MUST exactly match entity text from the list above
2. Use specific predicates — prefer HAS_PROPERTY over RELATES_TO
3. Extract ALL meaningful relationships, including implicit ones
4. For property-value chains, create two relationships: entity->HAS_PROPERTY->property, property->HAS_VALUE->measurement

Return ONLY the JSON array. If no relationships found, return []."""


async def extract_relationships(
    section: Section,
    entities: list[Entity],
    llm: LLMClient,
    ontology: OntologyRegistry,
    document_title: str = "",
    store: InstanceStore | None = None,
) -> list[Relationship]:
    """Extract relationships from a section, given pre-extracted entities."""
    if not entities or not section.text.strip():
        return []

    # Format entity list for the prompt
    entity_list = "\n".join(f"- {e.text} ({e.entity_type})" for e in entities)

    # Build entity lookup for type resolution
    entity_lookup: dict[str, Entity] = {}
    for e in entities:
        entity_lookup[e.text.lower()] = e
        entity_lookup[normalize_entity_name(e.text)] = e

    # Get prompts from store or use fallback
    system_prompt = None
    user_template = None
    if store:
        system_prompt = store.get_prompt("relationship_extraction_system")
        user_template = store.get_prompt("relationship_extraction_user")

    if system_prompt and user_template:
        user_prompt = safe_format(
            user_template,
            document_title=document_title,
            section_title=section.title,
            section_text=section.text,
            entities=entity_list,
            relationship_types=ontology.format_relationship_types(),
        )
    else:
        system_prompt = _FALLBACK_SYSTEM
        user_prompt = _FALLBACK_USER.format(
            document_title=document_title,
            section_title=section.title,
            section_text=section.text,
            entities=entity_list,
            relationship_types=ontology.format_relationship_types(),
        )

    # Domain knowledge context injection
    domain_context = store.get_prompt("domain_knowledge_context") if store else None
    if domain_context:
        user_prompt = (
            "DOMAIN KNOWLEDGE CONTEXT (use this to interpret abbreviations, "
            "terminology, and conventions):\n" + domain_context + "\n\n" + user_prompt
        )

    try:
        result = await llm.complete_json_lenient(
            system=system_prompt, user=user_prompt, max_tokens=8192
        )
    except Exception:
        logger.error("Relationship extraction failed for section '%s'", section.title)
        return []

    if isinstance(result, dict):
        for key in ("relationships", "data", "result"):
            if key in result and isinstance(result[key], list):
                result = result[key]
                break
        else:
            # Single relationship dict — wrap in list
            if "subject" in result or "predicate" in result:
                result = [result]
            else:
                result = []

    relationships: list[Relationship] = []

    for item in result:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject", "").strip()
        predicate = item.get("predicate", "").upper().strip()
        obj = item.get("object", "").strip()
        confidence = float(item.get("confidence", 0.5))

        if not subject or not predicate or not obj:
            continue

        # Resolve entity types
        subj_entity = entity_lookup.get(subject.lower()) or entity_lookup.get(
            normalize_entity_name(subject)
        )
        obj_entity = entity_lookup.get(obj.lower()) or entity_lookup.get(normalize_entity_name(obj))

        if not subj_entity:
            logger.debug("Subject '%s' not found in entities, skipping", subject)
            continue
        if not obj_entity:
            logger.debug("Object '%s' not found in entities, skipping", obj)
            continue

        rel = Relationship(
            subject=subj_entity.text,
            subject_type=subj_entity.entity_type,
            predicate=predicate,
            object=obj_entity.text,
            object_type=obj_entity.entity_type,
            confidence=confidence,
            source_doc=document_title,
            source_section=section.node_id,
        )
        relationships.append(rel)

    logger.info("Extracted %d relationships from section '%s'", len(relationships), section.title)
    return relationships
