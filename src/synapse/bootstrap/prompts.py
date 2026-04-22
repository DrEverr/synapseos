"""Predefined general prompts for the bootstrap pipeline.

These are the ONLY hardcoded prompts in the system. They are domain-agnostic and
designed to discover ontology structure from any batch of documents, then generate
domain-specific prompts for extraction and reasoning.

The bootstrap pipeline:
1. DOMAIN_ANALYSIS — analyze sample pages to identify the field/domain
2. ONTOLOGY_DISCOVERY — extract entity types and relationship types from the documents
3. ONTOLOGY_REFINEMENT — merge, deduplicate, and finalize the ontology
4. PROMPT_GENERATION — generate domain-specific prompts for entity extraction,
   relationship extraction, reasoning, enrichment, and tree search
"""

from __future__ import annotations


# ═══════════════════════════════════════════════════════════════
# Step 1: Domain Analysis
# ═══════════════════════════════════════════════════════════════

DOMAIN_ANALYSIS_SYSTEM = "You are a document analysis expert. You identify the domain, field, and subject matter of documents."

DOMAIN_ANALYSIS_USER = """Analyze the following sample pages from a batch of documents.
Identify the domain, field, and key subject areas these documents cover.

SAMPLE PAGES:
{sample_text}

Return a JSON object with:
- "domain": The primary domain/field (e.g., "culinary arts", "industrial chemistry", "medicine", "law", "finance")
- "subdomain": More specific area within the domain (e.g., "Polish cuisine", "silicone coatings", "cardiology")
- "language": Primary language of the documents (e.g., "English", "Polish", "German")
- "document_types": Array of document types found (e.g., ["cookbook", "recipe collection"], ["technical data sheet", "safety data sheet"])
- "key_topics": Array of 5-10 key topics/themes found in the documents
- "complexity": "basic", "intermediate", or "advanced" — how technical/specialized the content is
- "scientific_aspects": Array of 3-10 theoretical or mechanistic concepts that underpin this domain —
  the principles explaining why and how things work, beyond what the documents explicitly state

Return ONLY the JSON, nothing else."""


# ═══════════════════════════════════════════════════════════════
# Step 2: Ontology Discovery
# ═══════════════════════════════════════════════════════════════

ONTOLOGY_DISCOVERY_SYSTEM = """You are an ontology engineering expert. You design knowledge graph schemas by analyzing domain documents.
Your task is to identify the entity types and relationship types that would best capture the knowledge in these documents."""

ONTOLOGY_DISCOVERY_USER = """Analyze these document samples and design an ontology (entity types and relationship types) for a knowledge graph.

DOMAIN CONTEXT:
{domain_context}

SAMPLE PAGES:
{sample_text}

{table_schemas}

CONSTRAINTS:
- Maximum {max_entity_types} entity types
- Maximum {max_rel_types} relationship types
- Entity types should be UPPERCASE_WITH_UNDERSCORES (e.g., INGREDIENT, CHEMICAL_COMPOUND)
- Relationship types should be UPPERCASE_WITH_UNDERSCORES (e.g., HAS_PROPERTY, USES_TECHNIQUE)
- Always include a generic RELATES_TO relationship as a last resort
- Each type needs a clear, concise description

GUIDELINES:
- Entity types should be concrete, extractable things (nouns) — not abstract concepts
- Relationship types should represent meaningful connections between entity types
- Prefer specific types over generic ones (INGREDIENT over ITEM, HAS_TEMPERATURE over HAS_VALUE)
- Think about what multi-hop queries a user would want to ask and design for those paths
- Include types for measurable properties, conditions, and classifications
- Include types for provenance (source documents, organizations, standards)
- Ensure outcome relationships (states, behaviors, effects) are traceable to the specific
  entity that produces them — not only to a shared intermediate node (process, event, condition)
  that is common across many different subjects
- If two different entities produce different outcomes under the same condition, that difference
  must be visible in the graph — add direct subject→outcome relationship types or a qualifying
  relationship (e.g., APPLIES_TO) so outcomes are never ambiguous about which entity they belong to
- Include types for the underlying mechanisms, processes, and principles that explain why domain
  outcomes occur — not just the outcomes themselves
- Include types for risks, failure conditions, and constraints inherent to this domain
- Include types for quantifiable thresholds, ranges, and conditions that govern domain behavior
- Include types for compatibility, substitutability, and exclusion relationships between domain elements
- CRITICAL for tabular data: if a TABLE SCHEMAS section is present above, treat each NUMERIC column
  header as a candidate for a HAS_* relationship type (e.g., a column "Weight kg" → HAS_WEIGHT,
  a column "Flow rate m³/h" → HAS_FLOW_RATE). For IDENTIFIER columns, ensure the entity type
  description mandates including that column value in the entity text. Tables are often the primary
  structured data source — if columns don't map to relationship types, the data is lost.

CRITICAL — Avoid shared-node value loss:
- Entities are stored via MERGE on their text/name. If multiple subjects share the same
  property NAME (e.g., "viscosity"), they will collapse into ONE node and values are lost.
- For any entity type that carries a measurable value, the entity text MUST include both
  the property name AND its value+unit, e.g., "viscosity: 100 mPa·s" not just "viscosity".
- This ensures each subject gets its own node with its own value after MERGE.
- WRONG entity design: type=PHYSICAL_PROPERTY, text="viscosity" (shared across all products)
- RIGHT entity design: type=PHYSICAL_PROPERTY, text="viscosity: 100 mPa·s at 25°C" (unique per product)
- General rule: if a query "What is property X of subject Y?" would require more than 1 hop
  from subject to value, the ontology needs flattening — the value must be reachable in 1 hop.

Return a JSON object with:
- "entity_types": object mapping TYPE_NAME to description string
- "relationship_types": object mapping TYPE_NAME to description string
- "reasoning": brief explanation of your design choices (2-3 sentences)

Return ONLY the JSON, nothing else."""


# ═══════════════════════════════════════════════════════════════
# Step 2b: Ontology Discovery from additional sample batches
# ═══════════════════════════════════════════════════════════════

ONTOLOGY_DISCOVERY_INCREMENTAL_USER = """Continue analyzing more document samples. You have already discovered these types from previous samples.

DOMAIN CONTEXT:
{domain_context}

EXISTING ENTITY TYPES:
{existing_entity_types}

EXISTING RELATIONSHIP TYPES:
{existing_relationship_types}

NEW SAMPLE PAGES:
{sample_text}

{table_schemas}

CONSTRAINTS:
- Maximum {max_entity_types} entity types total (including existing)
- Maximum {max_rel_types} relationship types total (including existing)
- Keep ALL existing types unless they are clearly wrong
- Add NEW types only if they capture knowledge not covered by existing types
- Merge similar types (e.g., if you see TEMPERATURE and TEMPERATURE_RANGE, keep the more useful one)

Return a JSON object with:
- "entity_types": COMPLETE object mapping ALL type names to descriptions (existing + new)
- "relationship_types": COMPLETE object mapping ALL type names to descriptions (existing + new)
- "changes": brief description of what was added/changed/merged

Return ONLY the JSON, nothing else."""


# ═══════════════════════════════════════════════════════════════
# Step 3: Ontology Refinement
# ═══════════════════════════════════════════════════════════════

ONTOLOGY_REFINEMENT_SYSTEM = (
    "You are an ontology engineering expert. You refine and finalize knowledge graph schemas."
)

ONTOLOGY_REFINEMENT_USER = """Review and refine this ontology for a {domain} knowledge graph.

ENTITY TYPES:
{entity_types}

RELATIONSHIP TYPES:
{relationship_types}

DOCUMENT TOPICS:
{key_topics}

{table_schemas}

REFINEMENT TASKS:
1. Remove types that overlap too much — merge them into the more general one
2. Ensure every entity type can participate in at least one relationship type
3. Ensure relationship types cover the key multi-hop reasoning paths:
   - Can a user trace from a specific item through intermediate concepts to reach a conclusion?
   - Are comparison queries possible (e.g., "compare X and Y on property Z")?
4. Add any missing types that would be critical for this domain
5. Ensure descriptions are clear and specific enough to guide an LLM during extraction
6. Cap at {max_entity_types} entity types and {max_rel_types} relationship types
7. Check for outcome ambiguity: for each type representing a result, state, or effect, ask
   "given this node and its neighbors, can I tell which specific entity causes it?" — if not,
   add a direct relationship from the primary subject entity to the outcome, or add a qualifying
   relationship type (e.g., APPLIES_TO) that links the outcome back to the entity it belongs to
8. Verify depth — the ontology should answer "why/how", not just "what": add types for underlying
   mechanisms, risks, quantifiable thresholds, or compatibility constraints if absent and relevant
9. Table completeness: if a TABLE SCHEMAS section is present above, verify that each
   NUMERIC column has a corresponding relationship type. If not, add one. Each row in
   a data table becomes an entity — its columns must be representable as properties or
   relationships in the ontology, otherwise that data is lost after extraction.
10. Check for shared-node value loss: for each entity type that carries a measurable value
   (property, measurement, dosage, etc.), verify the type description states that the entity
   text MUST include the value+unit — not just the property name. Entities are stored via MERGE
   on text, so "viscosity" from 10 products would collapse into one node losing all values.
   Correct: "viscosity: 100 mPa·s at 25°C". The description must make this explicit.

Return a JSON object with:
- "entity_types": refined mapping of TYPE_NAME to description
- "relationship_types": refined mapping of TYPE_NAME to description
- "changes_made": array of strings describing each change

Return ONLY the JSON, nothing else."""


# ═══════════════════════════════════════════════════════════════
# Step 4: Prompt Generation
# ═══════════════════════════════════════════════════════════════

PROMPT_GENERATION_SYSTEM = """You are an expert in LLM prompt engineering for knowledge extraction systems.
You write precise, domain-specific prompts that guide an LLM to extract structured knowledge from documents."""

PROMPT_GENERATION_USER = """Generate domain-specific prompts for a {domain} knowledge extraction system.

ONTOLOGY:
Entity types:
{entity_types}

Relationship types:
{relationship_types}

DOCUMENT CONTEXT:
- Language: {language}
- Document types: {document_types}
- Key topics: {key_topics}

Generate the following prompts as a JSON object with these keys:

1. "entity_extraction_system": System prompt for entity extraction
   - Should instruct the LLM to extract entities from a document section
   - Must reference the entity types above
   - Include domain-specific extraction rules (e.g., "prefer SPICE over INGREDIENT for herbs" in cooking)

2. "entity_extraction_user": User prompt template for entity extraction
   - Must contain placeholders: {{document_title}}, {{section_title}}, {{section_summary}}, {{entity_types}}, {{section_text}}
   - Should specify JSON output format: [{{"text", "entity_type", "confidence", "properties"}}]

3. "relationship_extraction_system": System prompt for relationship extraction
   - Should instruct the LLM to extract relationships between pre-extracted entities
   - Must reference the relationship types above

4. "relationship_extraction_user": User prompt template for relationship extraction
   - Must contain placeholders: {{document_title}}, {{section_title}}, {{section_text}}, {{entities}}, {{relationship_types}}
   - Should specify JSON output format: [{{"subject", "predicate", "object", "confidence"}}]

5. "reasoning_system": System prompt for the ReAct reasoning agent
   - Must describe the 8 tools (use EXACTLY these signatures):
     1. FIND(name) — Smart search for entities by name. Handles partial names, special characters, keyword fallback.
     2. DETAILS(name) — Get ALL properties and relationships of an entity in one call.
     3. RELATED(name, REL_TYPE) — Find entities connected by a relationship type. REL_TYPE is optional.
     4. COMPARE(name1, name2) — Compare two entities side by side: properties, shared connections.
     5. LIST(TYPE) — List all entities of a given type.
     6. SCHEMA() — Show all entity/relationship types with instance counts. Use first if unsure.
     7. SECTION_TEXT(section_id) — Retrieve full text of a document section.
     8. ANSWER(text) — Provide final answer (terminates reasoning).
   - Must contain placeholders: {{entity_types}}, {{relationship_types}}
   - Must specify the Thought/Action format (one action per step)
   - Must include a reasoning strategy: start with FIND, then DETAILS, use RELATED for exploration,
     COMPARE for comparisons, SCHEMA if unsure what types exist
   - Must include answer style rules: write for domain experts, never mention tool names or graph
     internals, reference product names and concrete data values, use markdown formatting
   - Do NOT mention Cypher, GRAPH_QUERY, or raw database queries — the tools abstract these away
   - Include 3-5 domain-specific example queries showing which tools to use

6. "reasoning_user": User prompt template for reasoning
   - Must contain placeholders: {{question}}, {{section_summaries}}

7. "tree_search_system": System prompt for document section retrieval
   - Brief: "You are a [domain] document section retrieval expert."

8. "tree_search_user": User prompt template for tree search
   - Must contain placeholders: {{query}}, {{document_trees}}

9. "enrichment_system": System prompt for post-answer graph enrichment
   - Brief: "You are an expert [domain] knowledge extraction system."

10. "enrichment_user": User prompt template for enrichment
    - Must contain placeholders: {{answer_text}}, {{question}}, {{entity_types}}, {{relationship_types}}, {{existing_entities}}

IMPORTANT RULES:
- Use double curly braces for template placeholders: {{placeholder}}
- Include domain-specific terminology and examples in all prompts
- Prompts should be in the same language as the documents ({language})... but system instructions in English
- The reasoning_system prompt must use the structured tools (FIND, DETAILS, RELATED, COMPARE, LIST, SCHEMA) — NOT raw Cypher/GRAPH_QUERY
- All prompts should enforce structured JSON output where applicable
- CRITICAL for entity_extraction prompts: for any entity type that carries a measurable value
  (physical properties, measurements, dosages, concentrations, etc.), the extraction prompt
  MUST instruct the LLM to include the value+unit in the "text" field, not just the property name.
  Example: text="viscosity: 100 mPa·s at 25°C" NOT text="viscosity".
  This is essential because entities are stored via MERGE on their text — if the value is only
  in "properties" dict, multiple subjects sharing the same property name will collapse into
  one node and all values except the first will be lost.
- CRITICAL for enrichment prompts: the enrichment_user prompt MUST require a "source_text"
  field in both entities and relationships JSON schemas — the exact sentence from the answer
  that supports the extraction (copied verbatim). This enables traceability in the review UI.

Return the JSON object with all 10 prompt keys. Return ONLY the JSON, nothing else."""


# ═══════════════════════════════════════════════════════════════
# Boilerplate detection prompt (used in extraction)
# ═══════════════════════════════════════════════════════════════

BOILERPLATE_KEYWORDS_DISCOVERY_SYSTEM = "You are a document structure analyst."

BOILERPLATE_KEYWORDS_DISCOVERY_USER = """Given these document types from the {domain} domain: {document_types}

What section titles or keywords indicate boilerplate content that should be SKIPPED during knowledge extraction?
(e.g., legal disclaimers, copyright notices, table of contents, indexes, author bios)

Return a JSON array of lowercase keywords/phrases that indicate boilerplate sections.
Example: ["legal", "disclaimer", "copyright", "index", "bibliography", "about the author"]

Return ONLY the JSON array, nothing else."""


# ═══════════════════════════════════════════════════════════════
# Step 5b: Domain Knowledge Context Generation
# ═══════════════════════════════════════════════════════════════

DOMAIN_KNOWLEDGE_GENERATION_SYSTEM = (
    "You are a domain knowledge summarization expert. You create concise reference "
    "guides that capture the essential background knowledge needed to understand "
    "technical documents in a specific field."
)

DOMAIN_KNOWLEDGE_GENERATION_USER = """Analyze the following sample pages from {domain} ({subdomain}) documents
written in {language}, covering topics: {key_topics}.

Create a concise DOMAIN KNOWLEDGE CONTEXT that captures cross-document knowledge
a human expert would have but that may not be explained in any single document.

Include:
1. TERMINOLOGY & ABBREVIATIONS: Key abbreviations, acronyms, and domain-specific
   terms with their definitions and full forms
2. STANDARDS & CONVENTIONS: Industry standards, classification systems, and
   normative references mentioned or implied
3. IMPLICIT RELATIONSHIPS: Concepts that documents assume the reader already
   knows are connected (e.g., material codes imply properties, product families
   share base characteristics)
4. NAMING PATTERNS: Systematic naming conventions, numbering schemes, product
   code structures

Keep the context under 2500 tokens. Be factual and specific — include actual
values, codes, and definitions found in the documents. Do not include generic
domain knowledge that any LLM would already know.

SAMPLE PAGES:
{sample_text}

Return the domain knowledge context as plain text (NOT JSON). Use clear
headers and bullet points for readability."""


# ═══════════════════════════════════════════════════════════════
# Step 5c: Domain Knowledge Context Update (after document processing)
# ═══════════════════════════════════════════════════════════════

DOMAIN_KNOWLEDGE_UPDATE_SYSTEM = (
    "You are a domain knowledge curator. You update a domain knowledge reference "
    "with new insights discovered from processing a document."
)

DOMAIN_KNOWLEDGE_UPDATE_USER = """A document has been processed and yielded the following extracted knowledge.
Update the existing domain knowledge context with any NEW insights.

EXISTING DOMAIN KNOWLEDGE CONTEXT:
{existing_context}

NEWLY PROCESSED DOCUMENT: "{document_title}"

EXTRACTED ENTITIES (by type):
{entity_summary}

KEY RELATIONSHIPS:
{relationship_summary}

RULES:
1. KEEP all existing knowledge unless it is factually incorrect
2. ADD new abbreviations, terminology, standards, or conventions discovered
3. ADD new implicit relationships or naming patterns observed
4. Do NOT add entity-specific facts — only add knowledge that would help
   interpret OTHER documents (cross-document knowledge)
5. Keep the total context under 2500 tokens
6. If there is nothing new to add, return the existing context UNCHANGED
7. Return the COMPLETE updated context as plain text (NOT JSON)"""


CONTRADICTION_SYSTEM = "You are an ontology expert. Your task is to identify contradictory relationship types."

CONTRADICTION_USER = """Given the following relationship types from a domain ontology, identify pairs that are logically contradictory or mutually exclusive — i.e., if relationship A holds between two entities, relationship B cannot hold between the same pair.

RELATIONSHIP TYPES:
{relationship_types}

Return a JSON array of pairs. Each pair is a two-element array [TYPE_A, TYPE_B].
Only include pairs where the contradiction is clear and semantically grounded.
Return [] if no contradictions are found.

Example output:
[["COMPATIBLE_WITH", "INCOMPATIBLE_WITH"], ["CAUSES", "PROTECTS_AGAINST"]]

Return ONLY the JSON array, nothing else."""
