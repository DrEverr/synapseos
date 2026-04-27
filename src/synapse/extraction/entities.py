"""LLM-based entity extraction from document sections.

Uses prompts from the InstanceStore (generated during bootstrap).
Falls back to hardcoded generic prompts if no generated prompts exist.
"""

from __future__ import annotations

import json
import logging
import re

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


_TABLE_ROW_RE = re.compile(r"^\|.+\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")


def detect_tables(text: str) -> list[dict]:
    """Detect markdown tables in *text*.

    Returns a list of dicts, each with:
      header   – the header row text
      row_count – number of data rows (excluding header and separator)
      start    – line index of the header row
      end      – line index after the last data row
      rows     – list of data-row strings (without header/separator)
    """
    # Strip <page_N> / </page_N> tags added by the structure parser
    text = re.sub(r"</?page_\d+>", "", text)
    lines = text.splitlines()
    tables: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Look for header row
        if _TABLE_ROW_RE.match(line):
            header_line = i
            line.count("|") - 1
            # Next line must be separator
            if i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()):
                sep_line = i + 1
                # Collect data rows
                data_rows: list[str] = []
                j = sep_line + 1
                while j < len(lines):
                    row = lines[j].strip()
                    if _TABLE_ROW_RE.match(row) and not _TABLE_SEP_RE.match(row):
                        data_rows.append(row)
                        j += 1
                    else:
                        break
                if data_rows:
                    tables.append({
                        "header": line,
                        "row_count": len(data_rows),
                        "start": header_line,
                        "end": j,
                        "rows": data_rows,
                    })
                i = j
                continue
        i += 1
    return tables


def split_table_section(section: Section, max_rows: int = 20) -> list[Section]:
    """Split a section containing a large table into sub-sections by row batches.

    Returns *[section]* unchanged when no table exceeds *max_rows*.
    """
    tables = detect_tables(section.text)
    if not tables or all(t["row_count"] <= max_rows for t in tables):
        return [section]

    lines = section.text.splitlines()
    sub_sections: list[Section] = []

    for tbl_idx, tbl in enumerate(tables):
        if tbl["row_count"] <= max_rows:
            continue

        # Context text = everything NOT inside this table
        pre_text = "\n".join(lines[: tbl["start"]])
        post_text = "\n".join(lines[tbl["end"] :])
        header_block = "\n".join(lines[tbl["start"] : tbl["start"] + 2])  # header + sep

        rows = tbl["rows"]
        for batch_idx in range(0, len(rows), max_rows):
            batch = rows[batch_idx : batch_idx + max_rows]
            batch_text = pre_text + "\n" + header_block + "\n" + "\n".join(batch)
            if post_text.strip():
                batch_text += "\n" + post_text

            sub = Section(
                title=section.title,
                start_page=section.start_page,
                end_page=section.end_page,
                node_id=f"{section.node_id}_tbl{tbl_idx}b{batch_idx // max_rows}",
                text=batch_text,
                summary=section.summary,
            )
            sub_sections.append(sub)

    # If we only split some tables, the caller still covers the rest via
    # the prompt-augmented extraction on the full section. For simplicity,
    # return ONLY the split sub-sections (each contains context + table batch).
    return sub_sections if sub_sections else [section]


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
5. For properties with values, the "text" field MUST include both the property name AND its value+unit
   (e.g., "viscosity: 100 mPa·s at 25°C" not just "viscosity") — this prevents value loss during storage
6. Do NOT extract generic property names without values (e.g., "density" alone is useless)

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

    # Domain knowledge context injection
    domain_context = store.get_prompt("domain_knowledge_context") if store else None
    if domain_context:
        user_prompt = (
            "DOMAIN KNOWLEDGE CONTEXT (use this to interpret abbreviations, "
            "terminology, and conventions):\n" + domain_context + "\n\n" + user_prompt
        )

    # Table-aware prompt augmentation
    tables = detect_tables(section.text)
    total_rows = 0
    if tables:
        total_rows = sum(t["row_count"] for t in tables)
        table_hint = (
            f"\n\nIMPORTANT: This section contains {len(tables)} data table(s) "
            f"with {total_rows} total data rows. Extract EVERY row as a separate "
            f"entity with properties mapped from column headers. Do NOT summarize, "
            f"skip, or sample rows. Each table row is a distinct entity.\n"
        )
        user_prompt = table_hint + user_prompt

    # Scale max_tokens with table size — each row needs ~200 tokens of JSON output
    if total_rows > 15:
        max_tokens = min(16384, 8192 + total_rows * 200)
    else:
        max_tokens = 8192

    try:
        result = await llm.complete_json_lenient(
            system=system_prompt, user=user_prompt, max_tokens=max_tokens
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
            # Single entity dict (has 'text' or 'entity_type') — wrap in list
            if "text" in result or "entity_type" in result:
                result = [result]
            else:
                logger.warning(
                    "Entity extraction returned dict without 'entities' key for '%s': keys=%s",
                    section.title,
                    list(result.keys()),
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
