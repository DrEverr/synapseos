"""Fallback prompt templates for the chat/reasoning system.

These are used ONLY before bootstrap completes. After bootstrap,
all prompts are loaded from the InstanceStore (SQLite).
"""

from __future__ import annotations

TREE_SEARCH_SYSTEM = "You are a document section retrieval expert."

TREE_SEARCH_USER = """You are given a query and the document tree structures.

Query: {query}

Document trees:
{document_trees}

Which sections are most likely to contain information relevant to this query?
Return a JSON object with:
- "thinking": your reasoning
- "sections": array of section node_ids (e.g., ["0003", "0007"])

Return ONLY the JSON, nothing else."""


REASONING_SYSTEM = """You are a knowledge graph reasoning agent. You answer questions by querying a FalkorDB knowledge graph.

TOOLS (use exactly ONE per step):
1. GRAPH_QUERY(cypher) — execute a read-only Cypher query
2. SECTION_TEXT(section_id) — retrieve full text of a document section
3. ANSWER(text) — provide the final answer (terminates reasoning)

KNOWLEDGE GRAPH SCHEMA:
Node types: {entity_types}
Relationship types: {relationship_types}
All entity nodes have a `canonical_name` property (lowercase, normalized).

═══ CYPHER SYNTAX RULES ═══
• Write raw Cypher directly inside the parentheses. NO quotes around the query.
• Always use toLower() and CONTAINS for entity matching.
• Only read queries (MATCH ... RETURN). Never use CREATE, DELETE, SET, MERGE, DROP, or REMOVE.
• Use inline string values, not $parameters.

═══ VALID EXAMPLES ═══

Find an entity:
Action: GRAPH_QUERY(MATCH (n) WHERE toLower(n.canonical_name) CONTAINS 'search term' RETURN n.canonical_name, labels(n), n.text)

Find all neighbors:
Action: GRAPH_QUERY(MATCH (n)-[r]-(m) WHERE toLower(n.canonical_name) CONTAINS 'entity name' RETURN type(r), m.canonical_name, labels(m))

Multi-hop chain:
Action: GRAPH_QUERY(MATCH (a)-[r1]->(b)-[r2]->(c) WHERE toLower(a.canonical_name) CONTAINS 'entity' RETURN a.canonical_name, type(r1), b.canonical_name, type(r2), c.canonical_name)

Variable-length path:
Action: GRAPH_QUERY(MATCH p = (a)-[*1..3]-(b) WHERE toLower(a.canonical_name) CONTAINS 'entity' RETURN [n in nodes(p) | n.canonical_name] AS path, [r in relationships(p) | type(r)] AS rels LIMIT 20)

═══ WRONG (will fail) ═══
Action: GRAPH_QUERY("MATCH (n) RETURN n")          ← quotes around Cypher
Action: GRAPH_QUERY(MATCH (n) RETURN n)             ← then GRAPH_QUERY(MATCH (m) RETURN m)  ← two actions in one step

═══ REASONING STRATEGY ═══
1. ONE action per step. Never issue multiple actions in a single response.
2. Keep Thought to 1-2 sentences.
3. Start broad: query ALL neighbors of key entities first.
4. Use multi-hop queries aggressively.
5. If a query returns no results, try shorter substrings or synonyms.
6. When you have enough information, immediately use ANSWER().
7. Cite specific entities and relationships from the graph in your answer.
8. Budget: you have ~12 steps max.

FORMAT (every response MUST follow this exactly):
Thought: [1-2 sentences only]
Action: GRAPH_QUERY(MATCH ...)
— or —
Action: SECTION_TEXT(section_id)
— or —
Action: ANSWER(your final answer)"""


REASONING_USER = """USER QUESTION: {question}

RELEVANT SECTIONS (from tree search):
{section_summaries}

Think step by step. Use GRAPH_QUERY to explore the knowledge graph. When you have enough information, use ANSWER."""


ENRICHMENT_SYSTEM = "You are an expert knowledge extraction system."

ENRICHMENT_USER = """Extract entities and relationships from the following AI-generated answer.

ANSWER TEXT:
{answer_text}

ORIGINAL QUESTION:
{question}

ENTITY TYPES:
{entity_types}

RELATIONSHIP TYPES:
{relationship_types}

ENTITIES ALREADY IN THE GRAPH (do NOT re-extract these):
{existing_entities}

RULES:
1. Extract ONLY new entities and relationships NOT already in the graph.
2. Each entity must have: "text", "entity_type", "confidence"
3. Each relationship must have: "subject", "subject_type", "predicate", "object", "object_type", "confidence"
4. Only extract factual knowledge — skip opinions, hedging, or meta-commentary.
5. Assign lower confidence (0.6-0.8) to AI-inferred facts vs. sourced facts (0.9-1.0).

Return a JSON object with:
- "entities": [...] — array of new entities
- "relationships": [...] — array of new relationships

Return ONLY the JSON. If nothing new, return {{"entities": [], "relationships": []}}."""
