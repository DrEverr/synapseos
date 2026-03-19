#!/usr/bin/env python3
"""Import cooking project ontology + prompts into SynapseOS3 as --graph cooking.

Reads:
  - All 4 ontology layers from the cooking project (base + inventory + physics + playbook)
  - All prompts from cooking/chat/prompts.py and cooking/extraction/{entities,relationships}.py

Creates:
  - ~/.synapse/cooking/instance.db with ontology version + prompts
  - Marks the instance as bootstrapped

Usage:
    python3 scripts/import_cooking.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# ── Paths ────────────────────────────────────────────────────
COOKING_ROOT = Path(__file__).resolve().parent.parent.parent / "cooking"
ONTOLOGY_DIR = COOKING_ROOT / "config" / "ontologies"

SYNAPSE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYNAPSE_ROOT / "src"))

from synapse.storage.instance_store import InstanceStore  # noqa: E402


# ── Load ontology from all 4 YAML layers ────────────────────
def load_cooking_ontology() -> tuple[dict[str, str], dict[str, str]]:
    """Merge all cooking ontology layers into flat {TYPE_NAME: description} dicts."""
    entity_types: dict[str, str] = {}
    relationship_types: dict[str, str] = {}

    layer_files = [
        "base.yaml",
        "cooking_inventory.yaml",
        "cooking_physics.yaml",
        "cooking_playbook.yaml",
    ]

    for filename in layer_files:
        path = ONTOLOGY_DIR / filename
        if not path.exists():
            print(f"  WARNING: {path} not found, skipping")
            continue

        with open(path) as f:
            data = yaml.safe_load(f)

        # Entity types — handle both dict and list-of-dict formats
        et = data.get("entity_types", {})
        if isinstance(et, dict):
            entity_types.update(et)
        elif isinstance(et, list):
            for item in et:
                if isinstance(item, dict) and "name" in item:
                    entity_types[item["name"]] = item.get("description", "")

        # Relationship types — handle both dict and list-of-dict formats
        rt = data.get("relationship_types", {})
        if isinstance(rt, dict):
            relationship_types.update(rt)
        elif isinstance(rt, list):
            for item in rt:
                if isinstance(item, dict) and "name" in item:
                    relationship_types[item["name"]] = item.get("description", "")

        print(f"  Loaded {filename}: {len(et)} entity types, {len(rt)} relationship types")

    return entity_types, relationship_types


# ── Build prompts ────────────────────────────────────────────
def build_cooking_prompts() -> dict[str, str]:
    """Build the 10 standard SynapseOS prompt keys from cooking project prompts."""

    # ── Entity extraction prompts ────────────────────────────
    entity_extraction_system = (
        "You are an expert entity extraction system for culinary and cooking documents."
    )
    entity_extraction_user = """Extract named entities from the following culinary text.

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

RULES:
1. Extract ALL culinary entities: ingredients, spices, tools, techniques, recipes, cuisines, measurements
2. Do NOT extract boilerplate (legal disclaimers, website navigation, ads)
3. Prefer specific types over generic ones (use SPICE over INGREDIENT for herbs and seasonings)
4. For measurements, include the unit in the text span (e.g., "200g", "2 cups", "1 tbsp")
5. For ingredients with preparation notes, extract the ingredient name (e.g., "cebula" not "cebula drobno posiekana")
6. Extract cooking temperatures as MEASUREMENT entities (e.g., "180\u00b0C", "350\u00b0F")
7. Extract cooking times as MEASUREMENT entities (e.g., "30 minutes", "2 hours")

Text to extract from:
"{section_text}"

Return ONLY the JSON array, nothing else. If no entities found, return []."""

    # ── Relationship extraction prompts ──────────────────────
    relationship_extraction_system = (
        "You are an expert relationship extraction system for culinary and cooking documents."
    )
    relationship_extraction_user = """Extract relationships between entities in the following culinary text.

DOCUMENT CONTEXT:
- Document: "{document_title}"
- Section: "{section_title}"

TEXT:
{section_text}

ENTITIES FOUND IN THIS TEXT:
{entity_list}

RELATIONSHIP TYPES:
{relationship_types}

RULES:
1. Each relationship must have: "subject", "predicate", "object", "confidence"
2. Subject and object must EXACTLY match an entity text from the list above
3. Predicate must be one of the relationship types listed above
4. Only extract relationships explicitly stated or strongly implied in the text
5. Prefer specific predicates over RELATES_TO \u2014 use RELATES_TO only as last resort
6. Key culinary relationships to look for:
   - Recipe HAS_INGREDIENT Ingredient
   - Recipe USES_TECHNIQUE Technique
   - Recipe BELONGS_TO_CUISINE Cuisine
   - Ingredient PAIRS_WITH Ingredient/Spice
   - Technique REQUIRES_TOOL Tool/Appliance
   - Ingredient CONTAINS_ALLERGEN Allergen

Return ONLY a valid JSON array, nothing else. If no relationships found, return []."""

    # ── Tree search prompts ──────────────────────────────────
    tree_search_system = "You are a culinary document section retrieval expert."
    tree_search_user = """You are given a query and the document tree structures from cookbooks and recipe collections.

Query: {query}

Document trees:
{document_trees}

Which sections are most likely to contain information relevant to this culinary query?
Return a JSON object with:
- "thinking": your reasoning
- "sections": array of section node_ids (e.g., ["0003", "0007"])

Return ONLY the JSON, nothing else."""

    # ── Reasoning prompts ────────────────────────────────────
    reasoning_system = """You are a culinary knowledge graph reasoning agent. You answer cooking questions by querying a FalkorDB knowledge graph.

TOOLS (use exactly ONE per step):
1. GRAPH_QUERY(cypher) \u2014 execute a read-only Cypher query
2. SECTION_TEXT(section_id) \u2014 retrieve full text of a document section
3. ANSWER(text) \u2014 provide the final answer (terminates reasoning)

KNOWLEDGE GRAPH SCHEMA:
Node types: {entity_types}
Relationship types: {relationship_types}
All entity nodes have a `canonical_name` property (lowercase, normalized).

DOMAIN: Culinary knowledge \u2014 ingredients, spices, recipes, cooking techniques, food science, kitchen tools, dietary restrictions, food safety.

\u2550\u2550\u2550 CYPHER SYNTAX RULES \u2550\u2550\u2550
\u2022 Write raw Cypher directly inside the parentheses. NO quotes around the query.
\u2022 Always use toLower() and CONTAINS for entity matching.
\u2022 Only read queries (MATCH ... RETURN). Never use CREATE, DELETE, SET, MERGE, DROP, or REMOVE.
\u2022 Use inline string values, not $parameters (the system does not bind parameters).

\u2550\u2550\u2550 VALID EXAMPLES \u2550\u2550\u2550

Find an ingredient:
Action: GRAPH_QUERY(MATCH (n) WHERE toLower(n.canonical_name) CONTAINS 'bazylia' RETURN n.canonical_name, labels(n), n.text)

Find all relationships of an entity:
Action: GRAPH_QUERY(MATCH (n)-[r]-(m) WHERE toLower(n.canonical_name) CONTAINS 'carbonara' RETURN type(r), m.canonical_name, labels(m))

Find recipe ingredients:
Action: GRAPH_QUERY(MATCH (r)-[:HAS_INGREDIENT]->(i) WHERE toLower(r.canonical_name) CONTAINS 'carbonara' RETURN i.canonical_name, labels(i))

Find what pairs with an ingredient:
Action: GRAPH_QUERY(MATCH (a)-[:PAIRS_WITH]-(b) WHERE toLower(a.canonical_name) CONTAINS 'pomidory' RETURN a.canonical_name, b.canonical_name, labels(b))

Multi-hop: ingredient -> allergen -> diet restrictions:
Action: GRAPH_QUERY(MATCH (a)-[r1]->(b)-[r2]->(c) WHERE toLower(a.canonical_name) CONTAINS 'maka pszenna' RETURN a.canonical_name, type(r1), b.canonical_name, type(r2), c.canonical_name)

Find recipes for a cuisine:
Action: GRAPH_QUERY(MATCH (r)-[:BELONGS_TO_CUISINE]->(c) WHERE toLower(c.canonical_name) CONTAINS 'wloska' RETURN r.canonical_name, labels(r))

Find techniques and their required tools:
Action: GRAPH_QUERY(MATCH (t)-[:REQUIRES_TOOL]->(tool) WHERE toLower(t.canonical_name) CONTAINS 'sous vide' RETURN t.canonical_name, tool.canonical_name, labels(tool))

\u2550\u2550\u2550 WRONG (will fail) \u2550\u2550\u2550
Action: GRAPH_QUERY("MATCH (n) RETURN n")          \u2190 quotes around Cypher
Action: GRAPH_QUERY(MATCH (n) RETURN n)             \u2190 then GRAPH_QUERY(MATCH (m) RETURN m)  \u2190 two actions in one step

\u2550\u2550\u2550 REASONING STRATEGY \u2550\u2550\u2550
1. ONE action per step. Never issue multiple actions in a single response.
2. Keep Thought to 1-2 sentences. Do NOT write internal monologues.
3. Start broad: query ALL neighbors of key entities first.
4. Use multi-hop queries to trace ingredient->technique->reaction chains.
5. For comparison questions: use UNION to compare in ONE query.
6. If a query returns no results, try shorter substrings or synonyms:
   - "maka pszenna" \u2192 "maka", "flour", "pszenna"
   - "pieprz czarny" \u2192 "pieprz", "pepper"
   - "sous vide" \u2192 "sous"
7. When you have enough information, immediately use ANSWER().
8. Cite specific entities and relationships from the graph in your answer.
9. Budget: you have ~12 steps max. Plan queries efficiently.

FORMAT (every response MUST follow this exactly):
Thought: [1-2 sentences only]
Action: GRAPH_QUERY(MATCH ...)
\u2014 or \u2014
Action: SECTION_TEXT(section_id)
\u2014 or \u2014
Action: ANSWER(your final answer)"""

    reasoning_user = """USER QUESTION: {question}

RELEVANT SECTIONS (from tree search):
{section_summaries}

Think step by step. Use GRAPH_QUERY to explore the culinary knowledge graph. When you have enough information, use ANSWER."""

    # ── Enrichment prompts ───────────────────────────────────
    enrichment_system = "You are an expert culinary knowledge extraction system."
    enrichment_user = """Extract culinary entities and relationships from the following AI-generated answer.
This knowledge will be added to a culinary knowledge graph.

ANSWER TEXT:
{answer_text}

ORIGINAL QUESTION:
{question}

ENTITY TYPES:
{entity_types}

RELATIONSHIP TYPES:
{relationship_types}

ENTITIES ALREADY IN THE GRAPH (do NOT re-extract these \u2014 only extract NEW knowledge):
{existing_entities}

RULES:
1. Extract ONLY new culinary entities and relationships that are NOT already in the graph.
2. Each entity must have: "text", "entity_type", "confidence"
3. Each relationship must have: "subject", "subject_type", "predicate", "object", "object_type", "confidence"
4. subject and object in relationships must exactly match an entity "text" from your extracted entities OR from the existing entities list.
5. Only extract factual culinary knowledge \u2014 skip opinions, hedging, or meta-commentary.
6. Assign lower confidence (0.6-0.8) to facts from general AI knowledge vs. sourced facts (0.9-1.0).
7. Do NOT extract vague or overly generic entities (e.g. "food", "cooking").
8. For techniques, processes, and reactions \u2014 extract them even if they don't appear in the ontology, using the closest matching type.

Return a JSON object with:
- "entities": [...] \u2014 array of new entities
- "relationships": [...] \u2014 array of new relationships

Return ONLY the JSON, nothing else. If nothing new to extract, return {{"entities": [], "relationships": []}}."""

    # ── Boilerplate keywords ─────────────────────────────────
    boilerplate_keywords = (
        "legal, disclaimer, warranty, liability, contact, address, imprint, "
        "copyright, trademark, privacy, terms of use, cookie, advertisement"
    )

    return {
        "entity_extraction_system": entity_extraction_system,
        "entity_extraction_user": entity_extraction_user,
        "relationship_extraction_system": relationship_extraction_system,
        "relationship_extraction_user": relationship_extraction_user,
        "tree_search_system": tree_search_system,
        "tree_search_user": tree_search_user,
        "reasoning_system": reasoning_system,
        "reasoning_user": reasoning_user,
        "enrichment_system": enrichment_system,
        "enrichment_user": enrichment_user,
        "boilerplate_keywords": boilerplate_keywords,
    }


# ── Main ─────────────────────────────────────────────────────
def main() -> None:
    graph_name = "cooking"
    instance_dir = Path.home() / ".synapse" / graph_name
    instance_dir.mkdir(parents=True, exist_ok=True)
    db_path = instance_dir / "instance.db"

    print(f"=== Importing cooking domain into SynapseOS3 ===")
    print(f"Instance: {instance_dir}")
    print(f"DB: {db_path}")
    print()

    # 1. Load ontology
    print("Loading cooking ontology (4 layers)...")
    entity_types, relationship_types = load_cooking_ontology()
    print(
        f"  Total: {len(entity_types)} entity types, {len(relationship_types)} relationship types"
    )
    print()

    # 2. Build prompts
    print("Building prompts...")
    prompts = build_cooking_prompts()
    print(f"  {len(prompts)} prompts built")
    print()

    # 3. Create instance store and import
    print("Creating instance store...")
    store = InstanceStore(db_path)

    # Build the import payload (same format as export_version)
    import_data = {
        "version": {
            "name": "cooking-full",
            "description": "Culinary Knowledge Graph — all 4 ontology layers (base + inventory + physics + playbook)",
            "domain": "Culinary Arts & Food Science",
        },
        "entity_types": entity_types,
        "relationship_types": relationship_types,
        "prompts": prompts,
    }

    version_id = store.import_version(import_data, activate=True)
    print(f"  Created ontology version: {version_id}")

    # 4. Mark as bootstrapped
    store.mark_bootstrapped(domain="Culinary Arts & Food Science")
    store.set_meta("subdomain", "Recipes, Ingredients, Techniques, Food Science, Kitchen Tools")
    store.set_meta("language", "Polish (with English translations)")
    print("  Marked as bootstrapped")

    # 5. Verify
    print()
    print("=" * 60)
    print("IMPORT COMPLETE")
    print("=" * 60)
    print(f"Graph name: {graph_name}")
    print(f"Domain: {store.get_meta('domain')}")
    print(f"Language: {store.get_meta('language')}")
    print(f"Active version: {store.get_active_version_id()}")
    print(f"Entity types: {len(store.get_entity_types(version_id))}")
    print(f"Relationship types: {len(store.get_relationship_types(version_id))}")
    print(f"Prompts: {len(store.get_all_prompts(version_id))}")

    print()
    print("Entity types:")
    for etype, desc in sorted(store.get_entity_types(version_id).items()):
        print(f"  {etype}: {desc[:80]}")

    print()
    print("Relationship types:")
    for rtype, desc in sorted(store.get_relationship_types(version_id).items()):
        print(f"  {rtype}: {desc[:80]}")

    print()
    print("Prompt keys:")
    for key in sorted(store.get_all_prompts(version_id).keys()):
        text = store.get_all_prompts(version_id)[key]
        print(f"  {key} ({len(text)} chars)")

    store.close()

    print()
    print("Next steps:")
    print(f"  synapse -g {graph_name} status         # verify instance")
    print(f"  synapse -g {graph_name} ingest <docs>   # ingest cookbooks")
    print(f"  synapse -g {graph_name} chat            # query the KG")


if __name__ == "__main__":
    main()
