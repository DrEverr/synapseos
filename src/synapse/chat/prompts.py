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
• IMPORTANT: Always add WHERE COALESCE(n.verified, true) = true for entity nodes to exclude unverified AI-generated data.

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


REASONING_USER = """{conversation_context}USER QUESTION: {question}

RELEVANT SECTIONS (from tree search):
{section_summaries}

Think step by step. Use GRAPH_QUERY to explore the knowledge graph. When you have enough information, use ANSWER.
If prior turns already fetched relevant data, reuse it instead of re-querying."""


SELF_ASSESSMENT_SYSTEM = "You are a critical evaluator of AI-generated answers."

SELF_ASSESSMENT_USER = """Evaluate the following AI-generated answer for quality and reliability.

QUESTION: {question}

ANSWER: {answer}

EVIDENCE USED (from reasoning trace):
{evidence_summary}

Assess the answer on these dimensions:
1. **confidence** (0.0-1.0): How confident are you that the answer is correct and complete?
2. **groundedness** (0.0-1.0): What fraction of claims are supported by evidence from the graph or documents (vs. generated/hallucinated)?
3. **completeness** (0.0-1.0): Does the answer fully address the question, or are parts missing?
4. **reasoning**: A 1-2 sentence explanation of your assessment.
5. **gaps**: A list of specific knowledge gaps that would improve the answer (empty list if none).

Return a JSON object with exactly these keys: "confidence", "groundedness", "completeness", "reasoning", "gaps".
Return ONLY the JSON."""


CHALLENGER_SYSTEM = "You are a skeptical expert reviewer. Your job is to find flaws, unsupported claims, and missing context in AI-generated answers. Be rigorous but fair."

CHALLENGER_USER = """Review the following AI-generated answer critically.

QUESTION: {question}

ANSWER: {answer}

EVIDENCE FROM KNOWLEDGE GRAPH:
{evidence_summary}

Your task:
1. Identify factual errors or unsupported claims (claims not backed by the evidence).
2. Find important aspects of the question that were not addressed.
3. Note any contradictions or logical inconsistencies.
4. Assess whether the answer would mislead a domain expert.

Return a JSON object:
{{
  "agree": true/false,
  "critique": "Your detailed critique (2-5 sentences)",
  "issues": ["issue 1", "issue 2", ...],
  "suggested_improvements": ["improvement 1", ...]
}}

Set "agree": true ONLY if the answer is accurate, well-grounded, and complete.
Return ONLY the JSON."""

REVISION_USER = """Your previous answer was reviewed by a challenger agent who found issues.

ORIGINAL QUESTION: {question}

YOUR PREVIOUS ANSWER: {previous_answer}

CHALLENGER'S CRITIQUE:
{critique}

ISSUES FOUND:
{issues}

SUGGESTED IMPROVEMENTS:
{improvements}

Please provide a REVISED answer that addresses all the issues raised.
You have access to the same knowledge graph. If you need more data, use GRAPH_QUERY.
When ready, provide your improved answer with Action: ANSWER(...)."""


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
2. Each entity must have: "text", "entity_type", "confidence", "source_text"
3. Each relationship must have: "subject", "subject_type", "predicate", "object", "object_type", "confidence", "source_text"
4. "source_text" = the exact sentence or phrase from the ANSWER TEXT that supports this extraction (copy verbatim).
5. Only extract factual knowledge — skip opinions, hedging, or meta-commentary.
6. Assign lower confidence (0.6-0.8) to AI-inferred facts vs. sourced facts (0.9-1.0).

Return a JSON object with:
- "entities": [...] — array of new entities
- "relationships": [...] — array of new relationships

Return ONLY the JSON. If nothing new, return {{"entities": [], "relationships": []}}."""


COMPACTION_SYSTEM = "You are a conversation summarizer for a knowledge graph reasoning system."

COMPACTION_USER = """Summarize the following conversation turns into a compact context block.
The summary will be injected into future prompts so the reasoning agent knows what was
already discussed and what data was already fetched from the graph.

CONVERSATION TURNS:
{turns_text}

Write a structured summary with these sections:
- **Goal**: What is the user trying to find out across these turns?
- **Key entities**: Entity names and types discovered (e.g., "Jan Kowalski (Person, CEO)")
- **Graph data fetched**: Important facts retrieved from the knowledge graph, as bullet points
- **Established context**: Resolved references and conclusions (e.g., "the company = Acme Corp")

RULES:
1. Keep it concise — under 300 words.
2. Preserve ALL entity names, types, and relationships exactly as they appeared.
3. Include Cypher patterns that returned useful data, so the agent can build on them.
4. Do NOT include your own reasoning or commentary — only factual summaries.
5. Write in the same language as the conversation."""
