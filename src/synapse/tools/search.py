"""Smart entity search with normalization and keyword fallback.

Works on any graph — no domain-specific knowledge required.
"""

from __future__ import annotations

import re
import logging

from synapse.storage.graph import GraphStore

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
    # Build keyword candidates: multi-word fragments from right (most specific)
    candidates = []
    # Full term minus noise
    clean = " ".join(words)
    if clean:
        candidates.append(clean)
    # Drop first word (often a brand prefix like "silres")
    if len(words) > 1:
        candidates.append(" ".join(words[1:]))
    # Individual words, longest first
    for w in sorted(words, key=len, reverse=True):
        if w not in candidates:
            candidates.append(w)
    return candidates


def smart_search(
    name: str,
    graph: GraphStore,
    limit: int = 20,
) -> list[dict]:
    """Search for entities by name with progressive fallback strategies.

    Returns list of dicts: {canonical_name, entity_type, confidence, source_docs}
    """
    normalized = normalize_search_term(name)
    if not normalized:
        return []

    # Strategy 1: full normalized name
    results = _search_contains(graph, normalized, limit)
    if results:
        logger.debug("FIND('%s'): found %d results with full term '%s'", name, len(results), normalized)
        return results

    # Strategy 2: progressive keyword fallback
    keywords = extract_keywords(normalized)
    for kw in keywords:
        if kw == normalized:
            continue  # already tried
        results = _search_contains(graph, kw, limit)
        if results:
            logger.debug("FIND('%s'): found %d results with keyword '%s'", name, len(results), kw)
            return results

    # Strategy 3: multi-word intersection (all keywords must match)
    words = [w for w in normalized.split() if w not in _NOISE_WORDS and len(w) > 1]
    if len(words) >= 2:
        results = _search_all_words(graph, words, limit)
        if results:
            logger.debug("FIND('%s'): found %d results with word intersection", name, len(results))
            return results

    logger.debug("FIND('%s'): no results found", name)
    return []


def _search_contains(graph: GraphStore, term: str, limit: int) -> list[dict]:
    """Search entities where canonical_name CONTAINS term."""
    rows = graph.query(
        "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
        "AND toLower(n.canonical_name) CONTAINS toLower($term) "
        f"RETURN n, labels(n)[0] "
        f"LIMIT {limit}",
        params={"term": term},
    )
    return _nodes_to_results(rows)


def _search_all_words(graph: GraphStore, words: list[str], limit: int) -> list[dict]:
    """Search entities where canonical_name CONTAINS ALL words."""
    conditions = " AND ".join(
        f"toLower(n.canonical_name) CONTAINS '{w}'" for w in words
    )
    rows = graph.query(
        f"MATCH (n) WHERE NOT n:Document AND NOT n:Section "
        f"AND {conditions} "
        f"RETURN n, labels(n)[0] "
        f"LIMIT {limit}",
    )
    return _nodes_to_results(rows)


def _nodes_to_results(rows: list) -> list[dict]:
    """Convert query results with (node, label) to result dicts."""
    results = []
    for r in rows:
        node, label = r[0], r[1]
        if not hasattr(node, "properties"):
            continue
        props = node.properties
        results.append({
            "canonical_name": props.get("canonical_name", ""),
            "entity_type": label,
            "confidence": props.get("confidence"),
            "source_docs": props.get("source_docs", ""),
        })
    # Sort by confidence descending (handle None)
    results.sort(key=lambda x: x.get("confidence") or 0, reverse=True)
    return results
