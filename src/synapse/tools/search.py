"""Smart entity search with normalization and keyword fallback.

Works on any graph — uses GraphToolsConfig for auto-discovered schema.
Searches across ALL text-like properties and deduplicates by node identity.
"""

from __future__ import annotations

import logging
import re

from synapse.storage.graph import GraphStore
from synapse.tools.config import GraphToolsConfig

logger = logging.getLogger(__name__)

_STRIP_CHARS = re.compile(r"[®™©°*\[\](){}\"'`]")
_NOISE_WORDS = {"the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "is", "at", "by", "with"}

# Properties that may contain searchable names/text
_NAME_FIELDS = ("canonical_name", "name", "text", "title", "label", "description")


def normalize_search_term(raw: str) -> str:
    """Normalize a search term: lowercase, strip special chars, collapse spaces."""
    text = _STRIP_CHARS.sub("", raw)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_keywords(term: str) -> list[str]:
    """Extract meaningful keywords from a normalized term, longest first."""
    words = [w for w in term.split() if w not in _NOISE_WORDS and len(w) > 1]
    candidates = []
    clean = " ".join(words)
    if clean:
        candidates.append(clean)
    if len(words) > 1:
        candidates.append(" ".join(words[1:]))
    for w in sorted(words, key=len, reverse=True):
        if w not in candidates:
            candidates.append(w)
    return candidates


def smart_search(
    name: str,
    graph: GraphStore,
    config: GraphToolsConfig,
    limit: int = 20,
) -> list[dict]:
    """Search for entities by name with progressive fallback strategies.

    Searches across ALL text-like fields (canonical_name, name, text, title, etc.)
    and deduplicates results by node identity (internal graph ID).
    """
    normalized = normalize_search_term(name)
    if not normalized:
        return []

    exclude = config.exclude_clause("n")

    # Discover which name fields exist on this graph's nodes
    fields = _discover_name_fields(graph, config)

    # Strategy 1: full normalized name across all fields
    results = _multi_field_search(graph, fields, exclude, normalized, limit)
    if results:
        logger.debug("FIND('%s'): found %d with full term '%s'", name, len(results), normalized)
        return results

    # Strategy 2: progressive keyword fallback
    keywords = extract_keywords(normalized)
    for kw in keywords:
        if kw == normalized:
            continue
        results = _multi_field_search(graph, fields, exclude, kw, limit)
        if results:
            logger.debug("FIND('%s'): found %d with keyword '%s'", name, len(results), kw)
            return results

    # Strategy 3: multi-word intersection (all words must match in at least one field)
    words = [w for w in normalized.split() if w not in _NOISE_WORDS and len(w) > 1]
    if len(words) >= 2:
        results = _multi_field_all_words(graph, fields, exclude, words, limit)
        if results:
            logger.debug("FIND('%s'): found %d with word intersection", name, len(results))
            return results

    logger.debug("FIND('%s'): no results found", name)
    return []


# Cache discovered fields per graph
_fields_cache: dict[str, list[str]] = {}


def _discover_name_fields(graph: GraphStore, config: GraphToolsConfig) -> list[str]:
    """Discover which of the known name fields actually exist on nodes in this graph."""
    cache_key = str(id(graph))
    if cache_key in _fields_cache:
        return _fields_cache[cache_key]

    exclude = config.exclude_clause("n")
    fields = []
    try:
        rows = graph.query(f"MATCH (n) WHERE {exclude} RETURN n LIMIT 1")
        if rows and hasattr(rows[0][0], "properties"):
            node_props = set(rows[0][0].properties.keys())
            for f in _NAME_FIELDS:
                if f in node_props:
                    fields.append(f)
    except Exception:
        pass

    if not fields:
        fields = [config.name_property]

    _fields_cache[cache_key] = fields
    logger.debug("Discovered searchable fields: %s", fields)
    return fields


def _multi_field_search(
    graph: GraphStore, fields: list[str], exclude: str, term: str, limit: int
) -> list[dict]:
    """Search across multiple fields with OR, deduplicate by node ID."""
    or_clause = " OR ".join(f"toLower(n.{f}) CONTAINS toLower($term)" for f in fields)
    where = f"{exclude} AND " if exclude else ""
    rows = graph.query(
        f"MATCH (n) WHERE {where}({or_clause}) "
        f"RETURN n, labels(n)[0], id(n) LIMIT {limit * 2}",
        params={"term": term},
    )
    return _dedup_nodes(rows, limit)


def _multi_field_all_words(
    graph: GraphStore, fields: list[str], exclude: str, words: list[str], limit: int
) -> list[dict]:
    """Search where ALL words match in at least one field each, deduplicate."""
    # For each word: (field1 CONTAINS word OR field2 CONTAINS word OR ...)
    word_clauses = []
    for w in words:
        or_parts = " OR ".join(f"toLower(n.{f}) CONTAINS '{w}'" for f in fields)
        word_clauses.append(f"({or_parts})")
    all_clause = " AND ".join(word_clauses)
    where = f"{exclude} AND " if exclude else ""
    rows = graph.query(
        f"MATCH (n) WHERE {where}{all_clause} "
        f"RETURN n, labels(n)[0], id(n) LIMIT {limit * 2}",
    )
    return _dedup_nodes(rows, limit)


def _dedup_nodes(rows: list, limit: int) -> list[dict]:
    """Deduplicate nodes by graph ID, return unique results sorted by confidence."""
    seen_ids: set = set()
    results = []
    for r in rows:
        node, label, node_id = r[0], r[1], r[2]
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        if not hasattr(node, "properties"):
            continue
        results.append({
            **node.properties,
            "entity_type": label,
        })
    results.sort(key=lambda x: x.get("confidence") or 0, reverse=True)
    return results[:limit]
