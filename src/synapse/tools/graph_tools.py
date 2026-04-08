"""Structured graph tools for AI reasoning — replaces raw Cypher queries.

Domain-agnostic: works on any graph by auto-discovering schema.
All name lookups are normalized and use smart search with fallbacks.
"""

from __future__ import annotations

import json
import logging

from synapse.storage.graph import GraphStore
from synapse.tools.search import smart_search, normalize_search_term

logger = logging.getLogger(__name__)


def execute_tool(tool: str, args: str, graph: GraphStore) -> str:
    """Dispatch a tool call and return formatted observation string."""
    tool = tool.upper()

    if tool == "FIND":
        return _tool_find(args, graph)
    elif tool == "DETAILS":
        return _tool_details(args, graph)
    elif tool == "RELATED":
        return _tool_related(args, graph)
    elif tool == "COMPARE":
        return _tool_compare(args, graph)
    elif tool == "LIST":
        return _tool_list(args, graph)
    elif tool == "SCHEMA":
        return _tool_schema(graph)
    else:
        return f"Unknown tool: {tool}. Available: FIND, DETAILS, RELATED, COMPARE, LIST, SCHEMA"


# ── FIND ─────────────────────────────────────────────────────

def _tool_find(name: str, graph: GraphStore) -> str:
    """Smart entity search with normalization and keyword fallback."""
    name = name.strip().strip("'\"")
    if not name:
        return "Error: FIND requires a name argument."

    results = smart_search(name, graph, limit=20)
    if not results:
        return f"No entities found matching '{name}'."

    lines = [f"Found {len(results)} result(s) for '{name}':"]
    for r in results:
        lines.append(f"  [{r['entity_type']}] {r['canonical_name']}  (conf: {r['confidence']})")
    return "\n".join(lines)


# ── DETAILS ──────────────────────────────────────────────────

def _tool_details(name: str, graph: GraphStore) -> str:
    """Get all properties and relationships of an entity."""
    name = name.strip().strip("'\"")
    if not name:
        return "Error: DETAILS requires a name argument."

    # Find the entity first
    resolved = _resolve_entity(name, graph)
    if not resolved:
        return f"Entity not found: '{name}'. Try FIND({name}) first."

    canonical, etype = resolved
    lines = [f"Entity: [{etype}] {canonical}"]

    # Node properties — fetch the full node object dynamically
    rows = graph.query(
        "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
        "AND n.canonical_name = $name RETURN n LIMIT 1",
        params={"name": canonical},
    )
    if rows and hasattr(rows[0][0], "properties"):
        node_props = rows[0][0].properties
        # Display all attributes (skip internal/empty ones)
        _SKIP = {"id", "canonical_name"}
        for k, v in sorted(node_props.items()):
            if k in _SKIP or v is None or v == "" or v == "{}":
                continue
            # Parse nested JSON (e.g., properties field with value/unit/condition)
            if k == "properties" and isinstance(v, str) and v != "{}":
                try:
                    nested = json.loads(v)
                    if nested:
                        for nk, nv in nested.items():
                            lines.append(f"  {nk}: {nv}")
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            lines.append(f"  {k}: {v}")

    # Outgoing relationships
    out_rels = graph.query(
        "MATCH (n)-[r]->(m) "
        "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
        "AND n.canonical_name = $name "
        "RETURN type(r), m.canonical_name, labels(m)[0] "
        "ORDER BY type(r)",
        params={"name": canonical},
    )
    if out_rels:
        lines.append(f"\n  Outgoing relationships ({len(out_rels)}):")
        for r in out_rels:
            lines.append(f"    -[{r[0]}]-> [{r[2]}] {r[1]}")

    # Incoming relationships
    in_rels = graph.query(
        "MATCH (m)-[r]->(n) "
        "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
        "AND n.canonical_name = $name "
        "RETURN type(r), m.canonical_name, labels(m)[0] "
        "ORDER BY type(r)",
        params={"name": canonical},
    )
    if in_rels:
        lines.append(f"\n  Incoming relationships ({len(in_rels)}):")
        for r in in_rels:
            lines.append(f"    [{r[2]}] {r[1]} -[{r[0]}]->")

    if not out_rels and not in_rels:
        lines.append("\n  (no relationships)")

    return "\n".join(lines)


# ── RELATED ──────────────────────────────────────────────────

def _tool_related(args: str, graph: GraphStore) -> str:
    """Find entities connected by optional relationship type."""
    parts = [p.strip().strip("'\"") for p in args.split(",", 1)]
    name = parts[0] if parts else ""
    rel_type = parts[1].strip().upper() if len(parts) > 1 else ""

    if not name:
        return "Error: RELATED requires at least a name argument. Usage: RELATED(name) or RELATED(name, REL_TYPE)"

    resolved = _resolve_entity(name, graph)
    if not resolved:
        return f"Entity not found: '{name}'. Try FIND({name}) first."

    canonical, _ = resolved

    if rel_type:
        # Filter by relationship type
        rows = graph.query(
            f"MATCH (n)-[r:{rel_type}]->(m) "
            "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
            "AND n.canonical_name = $name "
            "RETURN '→', type(r), m.canonical_name, labels(m)[0]",
            params={"name": canonical},
        )
        rows += graph.query(
            f"MATCH (m)-[r:{rel_type}]->(n) "
            "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
            "AND n.canonical_name = $name "
            "RETURN '←', type(r), m.canonical_name, labels(m)[0]",
            params={"name": canonical},
        )
    else:
        # All relationships
        rows = graph.query(
            "MATCH (n)-[r]->(m) "
            "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
            "AND n.canonical_name = $name "
            "RETURN '→', type(r), m.canonical_name, labels(m)[0]",
            params={"name": canonical},
        )
        rows += graph.query(
            "MATCH (m)-[r]->(n) "
            "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
            "AND n.canonical_name = $name "
            "RETURN '←', type(r), m.canonical_name, labels(m)[0]",
            params={"name": canonical},
        )

    if not rows:
        filter_msg = f" of type {rel_type}" if rel_type else ""
        return f"No relationships{filter_msg} found for '{canonical}'."

    lines = [f"Relationships for '{canonical}'" + (f" (type: {rel_type})" if rel_type else "") + f" — {len(rows)} found:"]
    for r in rows:
        direction, rtype, target, ttype = r
        lines.append(f"  {direction} [{rtype}] [{ttype}] {target}")
    return "\n".join(lines)


# ── COMPARE ──────────────────────────────────────────────────

def _tool_compare(args: str, graph: GraphStore) -> str:
    """Compare two entities side by side."""
    parts = [p.strip().strip("'\"") for p in args.split(",", 1)]
    if len(parts) < 2:
        return "Error: COMPARE requires two names. Usage: COMPARE(name1, name2)"

    name1, name2 = parts[0], parts[1]
    resolved1 = _resolve_entity(name1, graph)
    resolved2 = _resolve_entity(name2, graph)

    if not resolved1:
        return f"Entity not found: '{name1}'. Try FIND({name1}) first."
    if not resolved2:
        return f"Entity not found: '{name2}'. Try FIND({name2}) first."

    lines = [f"Comparing '{resolved1[0]}' vs '{resolved2[0]}':"]
    lines.append("")

    # Properties comparison — dynamic node attributes
    for label, (canonical, etype) in [("Entity 1", resolved1), ("Entity 2", resolved2)]:
        lines.append(f"  {label}: [{etype}] {canonical}")
        rows = graph.query(
            "MATCH (n) WHERE n.canonical_name = $name AND NOT n:Document AND NOT n:Section "
            "RETURN n LIMIT 1",
            params={"name": canonical},
        )
        if rows and hasattr(rows[0][0], "properties"):
            _SKIP = {"id", "canonical_name"}
            for k, v in sorted(rows[0][0].properties.items()):
                if k in _SKIP or v is None or v == "" or v == "{}":
                    continue
                if k == "properties" and isinstance(v, str) and v != "{}":
                    try:
                        nested = json.loads(v)
                        if nested:
                            for nk, nv in nested.items():
                                lines.append(f"    {nk}: {nv}")
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                lines.append(f"    {k}: {v}")

        # Relationships
        rels = graph.query(
            "MATCH (n)-[r]->(m) "
            "WHERE NOT n:Document AND NOT n:Section AND NOT m:Document AND NOT m:Section "
            "AND n.canonical_name = $name "
            "RETURN type(r), m.canonical_name",
            params={"name": canonical},
        )
        if rels:
            for r in rels:
                lines.append(f"    -[{r[0]}]-> {r[1]}")
        lines.append("")

    # Common connections
    shared = graph.query(
        "MATCH (a)-[r1]->(shared)<-[r2]-(b) "
        "WHERE NOT shared:Document AND NOT shared:Section "
        "AND a.canonical_name = $n1 AND b.canonical_name = $n2 "
        "RETURN shared.canonical_name, labels(shared)[0], type(r1), type(r2)",
        params={"n1": resolved1[0], "n2": resolved2[0]},
    )
    if shared:
        lines.append(f"  Shared connections ({len(shared)}):")
        for s in shared:
            lines.append(f"    [{s[1]}] {s[0]} (via {s[2]} / {s[3]})")

    return "\n".join(lines)


# ── LIST ─────────────────────────────────────────────────────

def _tool_list(args: str, graph: GraphStore) -> str:
    """List entities of a given type."""
    parts = [p.strip().strip("'\"") for p in args.split(",", 1)]
    entity_type = parts[0].upper() if parts else ""
    limit = 20
    if len(parts) > 1:
        try:
            limit = int(parts[1])
        except ValueError:
            pass

    if not entity_type:
        return "Error: LIST requires an entity type. Usage: LIST(PRODUCT) or LIST(PRODUCT, 50). Use SCHEMA() to see available types."

    rows = graph.query(
        f"MATCH (n:{entity_type}) "
        f"RETURN n "
        f"ORDER BY n.canonical_name LIMIT {limit}",
    )
    if not rows:
        return f"No entities of type '{entity_type}' found. Use SCHEMA() to see available types."

    lines = [f"Entities of type {entity_type} ({len(rows)} shown):"]
    for r in rows:
        node = r[0]
        if hasattr(node, "properties"):
            p = node.properties
            name = p.get("canonical_name", "?")
            conf = p.get("confidence", "?")
            lines.append(f"  {name}  (conf: {conf})")
        else:
            lines.append(f"  {r[0]}")
    return "\n".join(lines)


# ── SCHEMA ───────────────────────────────────────────────────

def _tool_schema(graph: GraphStore) -> str:
    """Auto-discover schema from graph: entity types + relationship types with counts."""
    entity_counts = graph.get_entity_counts()
    rel_counts = graph.get_relationship_counts()

    lines = ["Graph Schema:"]
    lines.append(f"\n  Entity types ({len(entity_counts)}):")
    for etype, count in sorted(entity_counts.items(), key=lambda x: -x[1]):
        lines.append(f"    {etype}: {count} instances")

    lines.append(f"\n  Relationship types ({len(rel_counts)}):")
    for rtype, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
        lines.append(f"    {rtype}: {count} instances")

    return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────

def _resolve_entity(name: str, graph: GraphStore) -> tuple[str, str] | None:
    """Resolve a name to (canonical_name, entity_type) using smart search."""
    normalized = normalize_search_term(name)
    if not normalized:
        return None

    # Exact match first
    rows = graph.query(
        "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
        "AND n.canonical_name = $name "
        "RETURN n.canonical_name, labels(n)[0] LIMIT 1",
        params={"name": normalized},
    )
    if rows:
        return rows[0][0], rows[0][1]

    # Smart search fallback
    results = smart_search(name, graph, limit=1)
    if results:
        return results[0]["canonical_name"], results[0]["entity_type"]

    return None
