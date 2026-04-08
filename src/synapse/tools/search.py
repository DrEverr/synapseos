"""Smart entity search with normalization and keyword fallback.

Works on any graph — uses GraphToolsConfig for auto-discovered schema.
"""

from __future__ import annotations

import re
import logging

from synapse.storage.graph import GraphStore
from synapse.tools.config import GraphToolsConfig

logger = logging.getLogger(__name__)

# Characters to strip from search terms
_STRIP_CHARS = re.compile(r"[®™©°*\[\](){}\"'`]")
# Common prefixes that can be dropped for keyword search
_NOISE_WORDS = {"the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "is", "at", "by", "with"}


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

    Returns list of dicts with all node properties + entity_type.
    """
    normalized = normalize_search_term(name)
    if not normalized:
        return []

    nprop = config.name_property
    exclude = config.exclude_clause("n")

    # Strategy 1: full normalized name
    results = _search_contains(graph, nprop, exclude, normalized, limit)
    if results:
        logger.debug("FIND('%s'): found %d with full term '%s'", name, len(results), normalized)
        return results

    # Strategy 2: progressive keyword fallback
    keywords = extract_keywords(normalized)
    for kw in keywords:
        if kw == normalized:
            continue
        results = _search_contains(graph, nprop, exclude, kw, limit)
        if results:
            logger.debug("FIND('%s'): found %d with keyword '%s'", name, len(results), kw)
            return results

    # Strategy 3: multi-word intersection
    words = [w for w in normalized.split() if w not in _NOISE_WORDS and len(w) > 1]
    if len(words) >= 2:
        results = _search_all_words(graph, nprop, exclude, words, limit)
        if results:
            logger.debug("FIND('%s'): found %d with word intersection", name, len(results))
            return results

    logger.debug("FIND('%s'): no results found", name)
    return []


def _search_contains(graph: GraphStore, nprop: str, exclude: str, term: str, limit: int) -> list[dict]:
    """Search entities where name property CONTAINS term."""
    where = f"{exclude} AND " if exclude else ""
    rows = graph.query(
        f"MATCH (n) WHERE {where}"
        f"toLower(n.{nprop}) CONTAINS toLower($term) "
        f"RETURN n, labels(n)[0] LIMIT {limit}",
        params={"term": term},
    )
    return _nodes_to_results(rows, nprop)


def _search_all_words(graph: GraphStore, nprop: str, exclude: str, words: list[str], limit: int) -> list[dict]:
    """Search entities where name property CONTAINS ALL words."""
    conditions = " AND ".join(f"toLower(n.{nprop}) CONTAINS '{w}'" for w in words)
    where = f"{exclude} AND " if exclude else ""
    rows = graph.query(
        f"MATCH (n) WHERE {where}{conditions} "
        f"RETURN n, labels(n)[0] LIMIT {limit}",
    )
    return _nodes_to_results(rows, nprop)


def _nodes_to_results(rows: list, nprop: str) -> list[dict]:
    """Convert query results with (node, label) to result dicts."""
    results = []
    for r in rows:
        node, label = r[0], r[1]
        if not hasattr(node, "properties"):
            continue
        props = node.properties
        results.append({
            **props,
            "entity_type": label,
        })
    # Sort by confidence descending if available
    results.sort(key=lambda x: x.get("confidence") or 0, reverse=True)
    return results
