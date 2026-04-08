"""Structured graph tools for AI reasoning — replaces raw Cypher queries.

Domain-agnostic: works on any graph by auto-discovering schema.
All name lookups are normalized and use smart search with fallbacks.
"""

from __future__ import annotations

import json
import logging

from synapse.storage.graph import GraphStore
from synapse.tools.config import GraphToolsConfig, discover_config
from synapse.tools.search import smart_search, normalize_search_term

logger = logging.getLogger(__name__)

# Module-level config cache per graph
_config_cache: dict[str, GraphToolsConfig] = {}


def get_config(graph: GraphStore) -> GraphToolsConfig:
    """Get or auto-discover config for this graph."""
    graph_id = id(graph)
    key = str(graph_id)
    if key not in _config_cache:
        _config_cache[key] = discover_config(graph)
    return _config_cache[key]


def execute_tool(tool: str, args: str, graph: GraphStore) -> str:
    """Dispatch a tool call and return formatted observation string."""
    tool = tool.upper()
    cfg = get_config(graph)

    if tool == "FIND":
        return _tool_find(args, graph, cfg)
    elif tool == "DETAILS":
        return _tool_details(args, graph, cfg)
    elif tool == "RELATED":
        return _tool_related(args, graph, cfg)
    elif tool == "COMPARE":
        return _tool_compare(args, graph, cfg)
    elif tool == "LIST":
        return _tool_list(args, graph, cfg)
    elif tool == "SCHEMA":
        return _tool_schema(graph)
    else:
        return f"Unknown tool: {tool}. Available: FIND, DETAILS, RELATED, COMPARE, LIST, SCHEMA"


# ── FIND ─────────────────────────────────────────────────────

def _tool_find(name: str, graph: GraphStore, cfg: GraphToolsConfig) -> str:
    name = name.strip().strip("'\"")
    if not name:
        return "Error: FIND requires a name argument."

    results = smart_search(name, graph, cfg, limit=20)
    if not results:
        return f"No entities found matching '{name}'."

    nprop = cfg.name_property
    lines = [f"Found {len(results)} result(s) for '{name}':"]
    for r in results:
        ename = r.get(nprop, r.get("canonical_name", r.get("name", "?")))
        etype = r.get("entity_type", "?")
        conf = r.get("confidence", "?")
        lines.append(f"  [{etype}] {ename}  (conf: {conf})")
    return "\n".join(lines)


# ── DETAILS ──────────────────────────────────────────────────

def _tool_details(name: str, graph: GraphStore, cfg: GraphToolsConfig) -> str:
    name = name.strip().strip("'\"")
    if not name:
        return "Error: DETAILS requires a name argument."

    # Find ALL matching entities
    matches = smart_search(name, graph, cfg, limit=10)
    if not matches:
        return f"Entity not found: '{name}'. Try FIND({name}) first."

    nprop = cfg.name_property
    sections = []
    for match in matches:
        canonical = match.get(nprop, match.get("canonical_name", match.get("name")))
        etype = match.get("entity_type", "?")
        if canonical:
            section = _entity_details(canonical, etype, graph, cfg)
            sections.append(section)

    if len(sections) == 1:
        return sections[0]
    return f"Found {len(sections)} matching entities:\n\n" + "\n\n---\n\n".join(sections)


def _entity_details(canonical: str, etype: str, graph: GraphStore, cfg: GraphToolsConfig) -> str:
    """Build detailed view of a single entity."""
    nprop = cfg.name_property
    exclude = cfg.exclude_clause("n")
    lines = [f"Entity: [{etype}] {canonical}"]

    # Fetch full node dynamically
    rows = graph.query(
        f"MATCH (n) WHERE {exclude} AND n.{nprop} = $name RETURN n LIMIT 1",
        params={"name": canonical},
    )
    if rows and hasattr(rows[0][0], "properties"):
        node_props = rows[0][0].properties
        _SKIP = {"id", nprop}
        for k, v in sorted(node_props.items()):
            if k in _SKIP or v is None or v == "" or v == "{}":
                continue
            if k == "properties" and isinstance(v, str):
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
    excl_both = cfg.exclude_clause("n") + " AND " + cfg.exclude_clause("m")
    out_rels = graph.query(
        f"MATCH (n)-[r]->(m) WHERE {excl_both} AND n.{nprop} = $name "
        f"RETURN type(r), m, labels(m)[0] ORDER BY type(r)",
        params={"name": canonical},
    )
    if out_rels:
        lines.append(f"\n  Outgoing relationships ({len(out_rels)}):")
        for r in out_rels:
            mname = _node_display_name(r[1], nprop)
            lines.append(f"    -[{r[0]}]-> [{r[2]}] {mname}")

    # Incoming relationships
    in_rels = graph.query(
        f"MATCH (m)-[r]->(n) WHERE {excl_both} AND n.{nprop} = $name "
        f"RETURN type(r), m, labels(m)[0] ORDER BY type(r)",
        params={"name": canonical},
    )
    if in_rels:
        lines.append(f"\n  Incoming relationships ({len(in_rels)}):")
        for r in in_rels:
            mname = _node_display_name(r[1], nprop)
            lines.append(f"    [{r[2]}] {mname} -[{r[0]}]->")

    if not out_rels and not in_rels:
        lines.append("\n  (no relationships)")

    return "\n".join(lines)


# ── RELATED ──────────────────────────────────────────────────

def _tool_related(args: str, graph: GraphStore, cfg: GraphToolsConfig) -> str:
    parts = [p.strip().strip("'\"") for p in args.split(",", 1)]
    name = parts[0] if parts else ""
    rel_type = parts[1].strip().upper() if len(parts) > 1 else ""

    if not name:
        return "Error: RELATED requires at least a name. Usage: RELATED(name) or RELATED(name, REL_TYPE)"

    resolved = _resolve_entity(name, graph, cfg)
    if not resolved:
        return f"Entity not found: '{name}'. Try FIND({name}) first."

    canonical, _ = resolved
    nprop = cfg.name_property
    excl_both = cfg.exclude_clause("n") + " AND " + cfg.exclude_clause("m")

    if rel_type:
        rows = graph.query(
            f"MATCH (n)-[r:{rel_type}]->(m) WHERE {excl_both} AND n.{nprop} = $name "
            f"RETURN '→', type(r), m, labels(m)[0]",
            params={"name": canonical},
        )
        rows += graph.query(
            f"MATCH (m)-[r:{rel_type}]->(n) WHERE {excl_both} AND n.{nprop} = $name "
            f"RETURN '←', type(r), m, labels(m)[0]",
            params={"name": canonical},
        )
    else:
        rows = graph.query(
            f"MATCH (n)-[r]->(m) WHERE {excl_both} AND n.{nprop} = $name "
            f"RETURN '→', type(r), m, labels(m)[0]",
            params={"name": canonical},
        )
        rows += graph.query(
            f"MATCH (m)-[r]->(n) WHERE {excl_both} AND n.{nprop} = $name "
            f"RETURN '←', type(r), m, labels(m)[0]",
            params={"name": canonical},
        )

    if not rows:
        filter_msg = f" of type {rel_type}" if rel_type else ""
        return f"No relationships{filter_msg} found for '{canonical}'."

    lines = [f"Relationships for '{canonical}'" + (f" (type: {rel_type})" if rel_type else "") + f" — {len(rows)} found:"]
    for r in rows:
        direction, rtype = r[0], r[1]
        mname = _node_display_name(r[2], nprop)
        mtype = r[3]
        lines.append(f"  {direction} [{rtype}] [{mtype}] {mname}")
    return "\n".join(lines)


# ── COMPARE ──────────────────────────────────────────────────

def _tool_compare(args: str, graph: GraphStore, cfg: GraphToolsConfig) -> str:
    parts = [p.strip().strip("'\"") for p in args.split(",", 1)]
    if len(parts) < 2:
        return "Error: COMPARE requires two names. Usage: COMPARE(name1, name2)"

    name1, name2 = parts[0], parts[1]
    resolved1 = _resolve_entity(name1, graph, cfg)
    resolved2 = _resolve_entity(name2, graph, cfg)

    if not resolved1:
        return f"Entity not found: '{name1}'. Try FIND({name1}) first."
    if not resolved2:
        return f"Entity not found: '{name2}'. Try FIND({name2}) first."

    nprop = cfg.name_property
    exclude = cfg.exclude_clause("n")
    excl_both = cfg.exclude_clause("n") + " AND " + cfg.exclude_clause("m")

    lines = [f"Comparing '{resolved1[0]}' vs '{resolved2[0]}':"]
    lines.append("")

    for label, (canonical, etype) in [("Entity 1", resolved1), ("Entity 2", resolved2)]:
        lines.append(f"  {label}: [{etype}] {canonical}")
        rows = graph.query(
            f"MATCH (n) WHERE {exclude} AND n.{nprop} = $name RETURN n LIMIT 1",
            params={"name": canonical},
        )
        if rows and hasattr(rows[0][0], "properties"):
            _SKIP = {"id", nprop}
            for k, v in sorted(rows[0][0].properties.items()):
                if k in _SKIP or v is None or v == "" or v == "{}":
                    continue
                if k == "properties" and isinstance(v, str):
                    try:
                        nested = json.loads(v)
                        if nested:
                            for nk, nv in nested.items():
                                lines.append(f"    {nk}: {nv}")
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                lines.append(f"    {k}: {v}")

        rels = graph.query(
            f"MATCH (n)-[r]->(m) WHERE {excl_both} AND n.{nprop} = $name "
            f"RETURN type(r), m",
            params={"name": canonical},
        )
        if rels:
            for r in rels:
                mname = _node_display_name(r[1], nprop)
                lines.append(f"    -[{r[0]}]-> {mname}")
        lines.append("")

    # Shared connections
    excl_shared = cfg.exclude_clause("shared")
    shared = graph.query(
        f"MATCH (a)-[r1]->(shared)<-[r2]-(b) "
        f"WHERE {excl_shared} AND a.{nprop} = $n1 AND b.{nprop} = $n2 "
        f"RETURN shared, labels(shared)[0], type(r1), type(r2)",
        params={"n1": resolved1[0], "n2": resolved2[0]},
    )
    if shared:
        lines.append(f"  Shared connections ({len(shared)}):")
        for s in shared:
            sname = _node_display_name(s[0], nprop)
            lines.append(f"    [{s[1]}] {sname} (via {s[2]} / {s[3]})")

    return "\n".join(lines)


# ── LIST ─────────────────────────────────────────────────────

def _tool_list(args: str, graph: GraphStore, cfg: GraphToolsConfig) -> str:
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

    nprop = cfg.name_property
    rows = graph.query(
        f"MATCH (n:{entity_type}) RETURN n ORDER BY n.{nprop} LIMIT {limit}",
    )
    if not rows:
        return f"No entities of type '{entity_type}' found. Use SCHEMA() to see available types."

    lines = [f"Entities of type {entity_type} ({len(rows)} shown):"]
    for r in rows:
        node = r[0]
        if hasattr(node, "properties"):
            p = node.properties
            name = p.get(nprop, p.get("name", "?"))
            conf = p.get("confidence", "?")
            lines.append(f"  {name}  (conf: {conf})")
        else:
            lines.append(f"  {r[0]}")
    return "\n".join(lines)


# ── SCHEMA ───────────────────────────────────────────────────

def _tool_schema(graph: GraphStore) -> str:
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

def _resolve_entity(name: str, graph: GraphStore, cfg: GraphToolsConfig) -> tuple[str, str] | None:
    """Resolve a name to (name_value, entity_type) using smart search."""
    normalized = normalize_search_term(name)
    if not normalized:
        return None

    nprop = cfg.name_property
    exclude = cfg.exclude_clause("n")

    # Exact match first
    rows = graph.query(
        f"MATCH (n) WHERE {exclude} AND n.{nprop} = $name "
        f"RETURN n.{nprop}, labels(n)[0] LIMIT 1",
        params={"name": normalized},
    )
    if rows:
        return rows[0][0], rows[0][1]

    # Smart search fallback
    results = smart_search(name, graph, cfg, limit=1)
    if results:
        rname = results[0].get(nprop, results[0].get("canonical_name", results[0].get("name")))
        return rname, results[0]["entity_type"]

    return None


def _node_display_name(node, nprop: str) -> str:
    """Extract display name from a node object or raw value."""
    if hasattr(node, "properties"):
        return node.properties.get(nprop, node.properties.get("name", str(node)))
    return str(node)
