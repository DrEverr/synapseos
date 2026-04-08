"""Auto-discovery configuration for graph tools.

Detects the name property and metadata labels by inspecting the graph.
No hardcoded assumptions about the schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from synapse.storage.graph import GraphStore

logger = logging.getLogger(__name__)

# Labels that are typically metadata/structural (not domain entities)
_KNOWN_META_LABELS = {"Document", "Section"}


@dataclass
class GraphToolsConfig:
    """Discovered configuration for graph tools."""

    name_property: str = "canonical_name"
    exclude_labels: set[str] = field(default_factory=lambda: {"Document", "Section"})

    def exclude_clause(self, var: str = "n") -> str:
        """Build a WHERE clause excluding metadata labels."""
        if not self.exclude_labels:
            return ""
        parts = [f"NOT {var}:{label}" for label in sorted(self.exclude_labels)]
        return " AND ".join(parts)


def discover_config(graph: GraphStore) -> GraphToolsConfig:
    """Auto-detect graph schema configuration.

    Discovers:
    - The name property used on entity nodes (canonical_name, name, title, etc.)
    - Which labels are metadata (Document, Section) vs domain entities
    """
    config = GraphToolsConfig()

    # Discover metadata labels: labels with structural properties (id, filename, tree_structure_json)
    try:
        all_labels = graph.query(
            "MATCH (n) RETURN DISTINCT labels(n)[0] AS label"
        )
        label_set = {r[0] for r in all_labels if r[0]}

        meta_labels = set()
        for label in label_set:
            if label in _KNOWN_META_LABELS:
                meta_labels.add(label)
                continue
            # Check if label has structural properties (filename, tree_structure_json)
            try:
                sample = graph.query(f"MATCH (n:{label}) RETURN n LIMIT 1")
                if sample and hasattr(sample[0][0], "properties"):
                    props = set(sample[0][0].properties.keys())
                    if "tree_structure_json" in props or "filename" in props:
                        meta_labels.add(label)
            except Exception:
                pass

        config.exclude_labels = meta_labels if meta_labels else _KNOWN_META_LABELS
    except Exception as e:
        logger.debug("Failed to discover labels: %s", e)

    # Discover name property: check what property entity nodes use for names
    try:
        exclude = config.exclude_clause("n")
        sample = graph.query(
            f"MATCH (n) WHERE {exclude} RETURN n LIMIT 1"
        )
        if sample and hasattr(sample[0][0], "properties"):
            props = sample[0][0].properties
            # Prefer canonical_name, fallback to name, text, title
            for candidate in ("canonical_name", "name", "text", "title", "label"):
                if candidate in props:
                    config.name_property = candidate
                    break
    except Exception as e:
        logger.debug("Failed to discover name property: %s", e)

    logger.debug(
        "Graph tools config: name_property=%s, exclude_labels=%s",
        config.name_property, config.exclude_labels,
    )
    return config
