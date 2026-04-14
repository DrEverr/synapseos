"""Entity resolution via fuzzy matching and deduplication."""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from synapse.models.entity import Entity
from synapse.resolution.normalizer import normalize_entity_name

logger = logging.getLogger(__name__)

# Patterns for extracting short abbreviation codes from entity names.
_ABBREV_PATTERNS = [
    re.compile(r"^([A-Za-z]{2,5})\s*[—–\-]\s*"),  # "FZ — förzinkat"
    re.compile(r"\(([A-Za-z]{2,5})\)"),  # "förzinkat (FZ)"
    re.compile(r"^([A-Z]{2,3})$"),  # "FZ" standalone
]


def _extract_abbreviation(name: str) -> str | None:
    """Extract a short abbreviation code from an entity name.

    Recognises patterns like ``FZ — förzinkat — C3``, ``förzinkat (FZ)``,
    or a bare ``FZ``.  Returns the lower-cased abbreviation or *None*.
    """
    for pat in _ABBREV_PATTERNS:
        m = pat.search(name)
        if m:
            return m.group(1).lower()
    return None


def are_same_entity(
    name_a: str,
    name_b: str,
    type_a: str = "",
    type_b: str = "",
    threshold: float = 0.90,
) -> bool:
    """Check if two entity names refer to the same entity.

    Uses three strategies:
    1. Exact canonical name match
    2. SequenceMatcher fuzzy ratio
    3. Prefix matching (min 4 chars)

    If types are provided and differ, entities are NOT the same.
    """
    if type_a and type_b and type_a != type_b:
        return False

    a = normalize_entity_name(name_a)
    b = normalize_entity_name(name_b)

    if not a or not b:
        return False

    # Exact match
    if a == b:
        return True

    # Fuzzy ratio — but reject if numeric identifiers differ
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= threshold:
        # Extract numbers from both names — if both have numbers and they differ, reject
        nums_a = set(re.findall(r"\d+", a))
        nums_b = set(re.findall(r"\d+", b))
        if nums_a and nums_b and nums_a != nums_b:
            pass  # Different product numbers (e.g., 1042 vs 1052) — not the same
        else:
            return True

    # Substring match (one is contained in the other)
    # Catches "bs 1042" inside "silres bs 1042"
    # The shorter string must be at least 4 chars AND at least 60% of the longer
    # to avoid false positives like "bs 104" matching both "bs 1042" and "bs 1052"
    min_len = min(len(a), len(b))
    max_len = max(len(a), len(b))
    if min_len >= 4 and min_len / max_len >= 0.35:
        shorter = a if len(a) <= len(b) else b
        longer = b if len(a) <= len(b) else a
        if shorter in longer:
            return True

    # Strategy 4: Abbreviation match (same type only)
    # "FZ" and "FZ — förzinkat — C3" both yield abbreviation "fz"
    if type_a and type_b and type_a == type_b:
        abbrev_a = _extract_abbreviation(name_a)
        abbrev_b = _extract_abbreviation(name_b)
        if abbrev_a and abbrev_b and abbrev_a == abbrev_b:
            return True

    return False


def resolve_entities(
    entities: list[Entity],
    threshold: float = 0.90,
) -> list[Entity]:
    """Deduplicate entities by canonical name and fuzzy matching.

    Pass 1: Group by exact (canonical_name, entity_type)
    Pass 2: Fuzzy-merge across groups

    Returns the deduplicated list, keeping the highest-confidence entity per group.
    """
    if not entities:
        return []

    # Normalize all names
    for entity in entities:
        entity.canonical_name = normalize_entity_name(entity.text)

    # Pass 1: Group by exact (canonical_name, entity_type)
    groups: dict[tuple[str, str], list[Entity]] = {}
    for entity in entities:
        key = (entity.canonical_name, entity.entity_type)
        groups.setdefault(key, []).append(entity)

    # Pass 2: Fuzzy-merge across groups
    merged_keys = list(groups.keys())
    merged: dict[tuple[str, str], list[Entity]] = {}
    visited: set[int] = set()

    for i, key_a in enumerate(merged_keys):
        if i in visited:
            continue
        group = list(groups[key_a])
        visited.add(i)

        for j, key_b in enumerate(merged_keys):
            if j in visited:
                continue
            if key_a[1] != key_b[1]:  # Different types
                continue
            if are_same_entity(key_a[0], key_b[0], key_a[1], key_b[1], threshold):
                group.extend(groups[key_b])
                visited.add(j)

        merged[key_a] = group

    # Pick best entity per group
    result: list[Entity] = []
    for group_entities in merged.values():
        best = max(group_entities, key=lambda e: e.confidence)
        # For abbreviation-merged groups, prefer the short code as canonical name
        if len(group_entities) > 1:
            abbrevs = [_extract_abbreviation(e.text) for e in group_entities]
            common = [a for a in abbrevs if a]
            if common:
                best.canonical_name = common[0].upper()
        # Merge source docs
        all_docs = {e.source_doc for e in group_entities if e.source_doc}
        if all_docs:
            best.source_doc = ", ".join(sorted(all_docs))
        result.append(best)

    logger.info(
        "Entity resolution: %d -> %d entities (threshold=%.2f)",
        len(entities),
        len(result),
        threshold,
    )
    return result
