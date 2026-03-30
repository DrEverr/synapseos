"""LLM-based entity extraction from document sections.

Uses prompts from the InstanceStore (generated during bootstrap).
Falls back to hardcoded generic prompts if no generated prompts exist.
"""

from __future__ import annotations

import json
import logging

from synapse.config import OntologyRegistry
from synapse.llm.client import LLMClient
from synapse.llm.templates import safe_format
from synapse.models.document import Section
from synapse.models.entity import Entity
from synapse.resolution.normalizer import normalize_entity_name
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)

# Default boilerplate keywords (used before bootstrap)
_DEFAULT_BOILERPLATE = [
    "legal",
    "disclaimer",
    "warranty",
    "liability",
    "contact",
    "address",
    "imprint",
    "copyright",
    "trademark",
    "index",
    "bibliography",
    "about the author",
    "table of contents",
]


def _get_boilerplate_keywords(store: InstanceStore | None) -> list[str]:
    """Get boilerplate keywords from the instance store or use defaults."""
    if store:
        raw = store.get_prompt("boilerplate_keywords")
        if raw:
            try:
                keywords = json.loads(raw)
                if isinstance(keywords, list):
                    return keywords
            except json.JSONDecodeError:
                pass
    return _DEFAULT_BOILERPLATE


def is_boilerplate_section(section: Section, store: InstanceStore | None = None) -> bool:
    """Check if a section is likely boilerplate that should be skipped."""
    keywords = _get_boilerplate_keywords(store)
    title_lower = section.title.lower()
    return any(kw in title_lower for kw in keywords)


# ── Fallback prompts (pre-bootstrap) ─────────────────────

_FALLBACK_SYSTEM = "You are an expert entity extraction system for technical documents."

_FALLBACK_USER = """Extract named entities from the following text.

DOCUMENT CONTEXT:
- Document: "{document_title}"
- Section: "{section_title}"
- Section summary: "{section_summary}"

ENTITY TYPES TO EXTRACT:
{entity_types}

OUTPUT FORMAT:
Return a JSON array. Each entity must have:
- "text": The exact text span from the input (copy verbatim)
- "entity_type": One of the types listed above (UPPERCASE)
- "confidence": Confidence score between 0.0 and 1.0
- "properties": (optional) Key-value pairs of properties for this entity
- "source_text": The exact sentence from the document where this entity appears (copy verbatim)

RULES:
1. Extract ALL entities, including numeric measurements with units
2. Do NOT extract boilerplate (legal disclaimers, company addresses)
3. Prefer specific types over generic ones
4. For measurements, include the unit in the text span
5. For properties with values, extract BOTH the property name AND its value as separate entities

Text to extract from:
"{section_text}"

Return ONLY the JSON array, nothing else. If no entities found, return []."""


async def extract_entities(
    section: Section,
    llm: LLMClient,
    ontology: OntologyRegistry,
    document_title: str = "",
    store: InstanceStore | None = None,
) -> list[Entity]:
    """Extract entities from a single document section using LLM."""
    if is_boilerplate_section(section, store):
        logger.debug("Skipping boilerplate section: %s", section.title)
        return []

    if not section.text.strip():
        logger.debug("Skipping section with empty text: %s", section.title)
        return []

    # Get prompts from store (generated) or use fallback
    system_prompt = None
    user_template = None
    if store:
        system_prompt = store.get_prompt("entity_extraction_system")
        user_template = store.get_prompt("entity_extraction_user")

    if system_prompt and user_template:
        # Use generated prompts (safe_format avoids choking on literal JSON braces)
        user_prompt = safe_format(
            user_template,
            document_title=document_title,
            section_title=section.title,
            section_summary=section.summary or "",
            entity_types=ontology.format_entity_types(),
            section_text=section.text,
        )
    else:
        # Fallback to hardcoded prompts
        system_prompt = _FALLBACK_SYSTEM
        user_prompt = _FALLBACK_USER.format(
            document_title=document_title,
            section_title=section.title,
            section_summary=section.summary or "",
            entity_types=ontology.format_entity_types(),
            section_text=section.text,
        )

    try:
        result = await llm.complete_json_lenient(
            system=system_prompt, user=user_prompt, max_tokens=4096
        )
    except Exception as e:
        logger.error("Entity extraction LLM call failed for section '%s': %s", section.title, e)
        return []

    # Log raw response for debugging
    logger.debug(
        "Entity extraction raw response for '%s': %s",
        section.title,
        str(result)[:500],
    )

    if isinstance(result, dict):
        for key in ("entities", "data", "result"):
            if key in result and isinstance(result[key], list):
                result = result[key]
                break
        else:
            logger.warning(
                "Entity extraction returned dict without 'entities' key for '%s': keys=%s",
                section.title,
                list(result.keys()) if isinstance(result, dict) else "N/A",
            )
            result = []

    entities: list[Entity] = []
    valid_types = set(ontology.entity_types.keys())
    skipped_no_text = 0
    skipped_no_type = 0

    for item in result:
        if not isinstance(item, dict):
            continue
        text = item.get("text", "").strip()
        etype = item.get("entity_type", "").upper().strip()
        confidence = float(item.get("confidence", 0.5))

        if not text:
            skipped_no_text += 1
            continue
        if not etype:
            skipped_no_type += 1
            continue

        if etype not in valid_types:
            logger.debug("Unknown entity type '%s' for '%s', keeping anyway", etype, text)

        entity = Entity(
            text=text,
            entity_type=etype,
            confidence=confidence,
            canonical_name=normalize_entity_name(text),
            properties=item.get("properties", {}),
            source_doc=document_title,
            source_section=section.node_id,
            source_text=item.get("source_text", ""),
        )
        entities.append(entity)

    if not entities:
        input_len = len(section.text) if section.text else 0
        logger.warning(
            "0 entities from section '%s' (input=%d chars, raw_items=%d, "
            "skipped_no_text=%d, skipped_no_type=%d, raw_type=%s)",
            section.title,
            input_len,
            len(result) if isinstance(result, list) else 0,
            skipped_no_text,
            skipped_no_type,
            type(result).__name__,
        )
    else:
        logger.info("Extracted %d entities from section '%s'", len(entities), section.title)

    return entities
