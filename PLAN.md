# PLAN.md — SynapseOS v0.6.0

Target version: **0.6.0**
Current version: 0.5.0
Focus: Better extraction of complex, structured documents (tables, catalogs, visual elements) while maintaining full domain-agnosticity.

## Motivation

Testing with the MANN+HUMMEL HVAC filter catalog (24-page product catalog with dense specification tables, material icons, and repeating product-page layout) revealed that the current pipeline extracts only ~14% of tabular data. The ontology schema was excellent, but data tables with 30-45 rows are largely missed. This is a systemic issue affecting any domain with specification tables, data sheets, or catalog-style documents.

---

## Phase 1: Table Detection & Structured Extraction

**Priority:** Critical
**Impact:** High — fixes the #1 extraction gap across all domains
**Files:** `src/synapse/parsers/pdf.py`, `src/synapse/extraction/pipeline.py`, `src/synapse/extraction/entities.py`

### 1.1 Table detection in PDF parser

- Use PyMuPDF's `page.find_tables()` API (available in pymupdf >= 1.23.0) to detect table regions on each page
- For each detected table, extract structured data: headers + rows
- Convert tables to clean markdown format with column headers
- Tag table regions in the page text so downstream extraction knows where tables are

**Deliverable:** `src/synapse/parsers/tables.py` — `detect_tables(page) -> list[Table]` where `Table` has `headers: list[str]`, `rows: list[list[str]]`, `page_number: int`, `markdown: str`

### 1.2 Table-aware section text

- When building section text, embed detected tables as markdown blocks with `<table>` tags
- Replace raw text-extracted table content (garbled columns) with clean markdown
- Preserve surrounding narrative text as-is

**Deliverable:** Updated `parsers/structure.py` — `pages_to_tagged_text()` includes clean table markdown

### 1.3 Table-aware extraction prompt augmentation

- When a section contains `<table>` tags, prepend an extraction instruction:
  "This section contains data tables. Extract EVERY row as a separate entity with properties mapped from column headers. Do not summarize, skip, or sample rows."
- This is injected at call time, not baked into generated prompts — keeps bootstrap generic

**Deliverable:** Updated `extraction/entities.py` — dynamic prompt augmentation when tables detected

### 1.4 Completeness verification for table sections

- After extraction, compare entity count against detected table row count
- If `extracted < expected * 0.7`, trigger a re-extraction pass for missing rows:
  "The following rows were not extracted. Extract them now: [remaining rows]"
- Limit to 2 re-extraction passes to avoid infinite loops

**Deliverable:** Updated `extraction/pipeline.py` — `_verify_table_completeness()` with retry logic

---

## Phase 2: Catalog-Aware Section Splitting

**Priority:** High
**Impact:** Medium — improves structure detection for repeating-page documents
**Files:** `src/synapse/parsers/structure.py`

### 2.1 Repeating layout pattern detection

- After page extraction, analyze layout similarity between pages (heading positions, table presence, image regions)
- Detect "product page" patterns: pages that share a common layout template
- Each product page (or page group) becomes its own top-level section

### 2.2 Fallback-aware integration

- Pattern detection runs as a pre-check before TOC detection
- If repeating pattern detected with confidence >= 0.8: use pattern-based splitting
- If not: fall back to existing TOC / LLM subdivision pipeline
- Hybrid: combine both (TOC for overall structure, pattern detection for within-category pages)

**Deliverable:** Updated `parsers/structure.py` — `detect_repeating_layout()` feeding into `extract_document_structure()`

---

## Phase 3: Visual Element Extraction

**Priority:** Medium
**Impact:** Medium — captures information encoded visually (icons, badges, diagrams)
**Files:** `src/synapse/parsers/pdf.py`, new `src/synapse/parsers/visual.py`

### 3.1 Page header annotation extraction

- Use PyMuPDF's text block positioning (`page.get_text("dict")`) to detect clusters of short text elements in page headers/margins
- Identify recurring icon-label patterns (e.g., material codes FZ/AZ/RF/SF/ZM in circles)
- Extract as structured annotations: `{"page": N, "header_labels": ["FZ", "AZ", "ZM"]}`

### 3.2 Vision model integration (optional)

- For pages with complex visual elements (diagrams, flow charts, annotated images), send page image to a multimodal LLM
- Query: "What structured information is shown in the visual elements on this page?"
- Merge visual extraction results with text extraction
- Configurable: `SYNAPSE_ENABLE_VISION_EXTRACTION=true` (off by default — adds cost)

**Deliverable:** `src/synapse/parsers/visual.py` — `extract_visual_annotations(page) -> list[Annotation]`

---

## Phase 4: Smarter Entity Resolution

**Priority:** Medium
**Impact:** Medium — reduces duplicate nodes
**Files:** `src/synapse/resolution/linker.py`

### 4.1 Type-aware fuzzy matching

- Current resolution uses a single global fuzzy threshold (0.90)
- Add per-type resolution strategies:
  - Same entity type + substring containment → merge aggressively (threshold 0.75)
  - Same entity type + same key property value → merge (e.g., two MATERIAL entities with "C5" corrosion class and "zinkmagnesium" substring)
  - Cross-type → never merge (keep strict 0.90)

### 4.2 Alias detection

- Detect common alias patterns: abbreviated vs full name, with/without codes
- E.g., `"aluzink (az) - c4"` and `"aluzink - c4"` — same entity, different verbosity
- Use entity type description to determine which form is "canonical"

**Deliverable:** Updated `resolution/linker.py` — type-aware resolution strategies

---

## Phase 5: Multi-Pass Extraction

**Priority:** Lower
**Impact:** Medium — catches entities missed in first pass
**Files:** `src/synapse/extraction/pipeline.py`

### 5.1 Gap detection pass

- After initial extraction of a section, send extracted entities + section text to LLM:
  "Review the section text. Are there entities you missed? Especially: table rows not extracted, relationships between items in different paragraphs, implicit facts."
- Only trigger if section text length > 2000 tokens (don't waste calls on short sections)

### 5.2 Cross-section relationship pass

- After all sections processed, take entity pairs that appear in adjacent sections
- Ask LLM to identify cross-section relationships
- Particularly useful for catalog documents where product pages reference accessories on other pages

**Deliverable:** Updated `extraction/pipeline.py` — optional `--thorough` flag for multi-pass

---

## Phase 6: Bootstrap Prompt Quality

**Priority:** Done (v0.5.0 patch)
**Status:** Implemented

### 6.1 Updated prompt generation template

- Bootstrap now generates reasoning prompts using structured tools (FIND, DETAILS, RELATED, COMPARE, LIST, SCHEMA) instead of legacy GRAPH_QUERY(cypher)
- Removes Cypher syntax rules and examples from prompt generation instructions
- Adds tool usage strategy and answer style guidelines
- Fallback prompts in `chat/prompts.py` already used new tools; now generated prompts match

### 6.2 Backward compatibility

- Runtime (`chat/reasoning.py`) still dispatches legacy `GRAPH_QUERY` for domains bootstrapped before this change
- No migration needed for existing instances — they continue working with old prompts
- Re-bootstrapping (`synapse init`) will generate new-style prompts

---

## Implementation Order

| # | Phase | Est. complexity | Depends on |
|---|-------|----------------|------------|
| 1 | Phase 6: Bootstrap Prompt Quality | Done | - |
| 2 | Phase 1.1-1.2: Table Detection | Medium | - |
| 3 | Phase 1.3: Table-aware prompts | Small | 1.1-1.2 |
| 4 | Phase 4: Entity Resolution | Small | - |
| 5 | Phase 2: Catalog Section Splitting | Medium | - |
| 6 | Phase 1.4: Completeness verification | Small | 1.3 |
| 7 | Phase 5: Multi-Pass Extraction | Medium | 1.4 |
| 8 | Phase 3: Visual Element Extraction | Large | - |

Phases 1, 2, 4 can be developed in parallel. Phase 3 is independent but largest.

---

## Testing Strategy

- **Phase 1:** Unit test table detection on known PDF pages; integration test comparing extracted entity count before/after on the MANN+HUMMEL catalog
- **Phase 2:** Test with repeating-layout documents (product catalogs) vs narrative documents (ensure no regression)
- **Phase 3:** Test with pages containing visual annotations; verify extraction accuracy
- **Phase 4:** Test resolution on known duplicate pairs; verify no false merges
- **Phase 5:** A/B comparison of extraction completeness with/without multi-pass

All tests must remain offline (no FalkorDB/API required) — mock LLM responses where needed.

---

## Success Criteria

Using the MANN+HUMMEL filter catalog (24 pages, ~140 size variants, 5 materials, 6 cabinet models) as benchmark:

| Metric | v0.5.0 (current) | v0.6.0 (target) |
|--------|-------------------|------------------|
| CABINET_SIZE_VARIANT extraction | 20/140 (14%) | > 120/140 (85%) |
| CONSTRUCTED_FROM relationships | 7 | > 25 |
| Material duplicates | 4 | 0 |
| TRANSITION_PIECE variants | 4 (generic) | > 30 (with dimensions) |
| FILTER_PRODUCT coverage | 8/~15 | > 12/~15 |

---

## Phase 7: Reasoning Agent Fixes (QA Benchmark Findings)

**Priority:** High  
**Impact:** High — critical bugs that block reliable evaluation and production use  
**Source:** QA benchmark 2026-04-20, mnh-det vs mnh-hyb, 20 questions  
**Files:** `src/synapse/chat/reasoning.py`, `src/synapse/chat/enrichment.py`, `src/synapse/storage/instance_store.py`

### 7.1 Fix asyncio timeout — CancelledError not propagating

**Problem:** `asyncio.wait_for(reason_full(...), timeout=180)` does not actually stop reasoning after 180s. Observed runaway questions: Q4 (568s), Q7 (449s), Q14 (761s), Q18 (1202s), Q10-hyb (1553s). Root cause: the HTTP client (`httpx`/`openai`) does not cancel in-flight requests when `asyncio.CancelledError` is sent — it waits for the server response, which can take 100–400s more. After the HTTP response arrives, the exception propagates and the task finally ends.

**Fix:** Use a per-step deadline check inside `reason_full` using `asyncio.timeout()` (Python ≥3.11) around each LLM call, so that cancellation is enforced at the HTTP layer. Also add a hard check at each step boundary.

```python
# In reason_full() — replace the bare LLM call in the ReAct loop:

import asyncio

for step in range(max_steps):
    elapsed = time.monotonic() - t0
    remaining = reasoning_timeout - elapsed
    if remaining <= 0:
        timed_out = True
        break

    try:
        async with asyncio.timeout(min(remaining, step_timeout)):
            response = await llm.complete_messages(
                messages=messages, temperature=0.0, max_tokens=step_max_tokens
            )
    except TimeoutError:
        # asyncio.timeout() raises TimeoutError (not CancelledError) in Python 3.11+
        logger.warning("Step %d timed out after %.0fs", step + 1, step_timeout)
        timed_out = True
        break
```

Where `step_timeout` defaults to 90s — gives LLM 90s per step, but overall loop is still bounded by `reasoning_timeout`. This makes timeout enforcement happen at the LLM call level, not at the asyncio task level, avoiding the httpx cancellation issue.

**Also fix:** `tests/run_qa_bench.py` should not use `asyncio.wait_for` — instead rely entirely on `reasoning_timeout` inside `reason_full`:

```python
# Before (doesn't work):
result = await asyncio.wait_for(reason_full(..., reasoning_timeout=300), timeout=180)

# After (correct):
result = await reason_full(..., reasoning_timeout=180)
```

### 7.2 Fix enrichment skipping valid relationships

**Problem:** During reasoning, the enrichment step creates entities and relationships from answers (e.g., `HAS_RATED_AIRFLOW`, `HAS_HOUSING_WEIGHT`, `COMPOSED_OF_MODULES`), but these predicates are not in the ontology — so they are silently dropped. This means every time the agent figures out "GDMI 1800x1800 has 30600 m³/h", this knowledge is lost.

From benchmark logs:
```
Skipping relationship with missing type/predicate: predicate='HAS_RATED_AIRFLOW' ...
Skipping relationship with missing type/predicate: predicate='HAS_HOUSING_WEIGHT' ...
Skipping relationship with missing type/predicate: predicate='COMPOSED_OF_MODULES' ...
```

**Fix option A — Add missing predicates to base ontology** (`config/ontologies/base.yaml`):
```yaml
relationship_types:
  HAS_RATED_AIRFLOW: "Relates a housing variant to its rated airflow capacity (m³/h)"
  HAS_HOUSING_WEIGHT: "Relates a housing variant to its weight in kg"
  COMPOSED_OF_MODULES: "Relates a housing to the filter module configuration it uses"
  ACCEPTS_FILTER_DEPTH: "Maximum filter depth (mm) accepted by a housing variant"
  HAS_LENGTH_VARIANT: "Relates a housing to its length variant (mm)"
```

**Fix option B — Store as properties instead of relationships:** In `enrichment.py`, when predicate is not in ontology, store the value as a property on the subject entity rather than dropping it:

```python
if predicate not in valid_rel_types:
    # Store as property on subject entity instead of relationship
    graph.update_entity_property(subject, predicate.lower(), obj)
    continue
```

Option A is simpler and more consistent. Option B is more resilient but requires a new `update_entity_property` method.

### 7.3 Fix self-assessment on empty answer

**Problem:** When `reason_full` times out before producing an answer, `answer = ""`. The self-assessment step then calls LLM with an empty answer, causing `Self-assessment LLM call failed: Empty input`.

**Fix:** In `reasoning.py`, guard the self-assessment call:

```python
# Before calling _self_assess:
if not answer.strip():
    answer = "(no answer — reasoning timed out or failed)"
    # Skip self-assessment on empty answers
    assessment = SelfAssessment(confidence=0.0, reasoning="Timed out or no answer produced")
else:
    assessment = await _self_assess(answer, question, actions_log, llm)
```

### 7.4 Add TABLE_QUERY tool to reasoning agent

**Problem:** 225 size-variant entities are missing from the KG (tables not extracted). Even after re-ingesting MÅTT pages, SQL-table access via `extracted_table_rows` would give the agent direct, reliable access to tabular data without relying on fuzzy graph traversal.

**New tool:** `TABLE_QUERY(product, filter_condition)` — queries `extracted_table_rows` SQLite store.

```python
# In src/synapse/tools/table_tools.py (new file):

def execute_table_query(args: str, store: InstanceStore) -> str:
    """Execute TABLE_QUERY(product_prefix, condition) against SQLite table store.
    
    Examples:
      TABLE_QUERY(GDMI, storlek_gdmi='600x600')
      TABLE_QUERY(GDMI, flode_3400_m3_h > 20000)
    """
    # Parse args: first token = table prefix, rest = WHERE condition
    parts = args.split(",", 1)
    prefix = parts[0].strip()
    condition = parts[1].strip() if len(parts) > 1 else None
    
    rows = store.query_table(source_doc_pattern=f"%{prefix}%", where=condition, limit=50)
    if not rows:
        return f"No table data found for {prefix!r}"
    return _format_table_rows(rows)
```

Register in `reason_full` dispatch:
```python
elif tool == "TABLE_QUERY":
    if store:
        result_text = execute_table_query(args, store)
    else:
        result_text = "(TABLE_QUERY not available — no instance store)"
    messages.append({"role": "user", "content": f"Table data:\n{result_text}"})
```

Add to system prompt: `TABLE_QUERY(product, condition) — look up raw tabular data (weights, airflows, dimensions) from the product catalog tables.`

---

## Phase 7b: Table-Aware Bootstrap (Ontology Discovery Gap)

**Priority:** High  
**Impact:** High — fixes the root cause of missing relationship types like `HAS_RATED_AIRFLOW`, `HAS_HOUSING_WEIGHT`, `COMPOSED_OF_MODULES`  
**Files:** `src/synapse/bootstrap/pipeline.py`, `src/synapse/bootstrap/prompts.py`

### Root Cause

Bootstrap (`synapse init`) sends raw markdown to `ONTOLOGY_DISCOVERY_USER`, including table content. The LLM sees:

```
| Storlek GDMI | Vikt kg 850/900 | Vikt kg 600/650 | Flöde 3400 m³/h |
| 300x300      | 26              | 21              | 850             |
```

It understands the concept "size variant exists" (from narrative text) and generates `HOUSING_SIZE_VARIANT`, but **does not map column headers to relationship types** because the prompt gives no such instruction. The LLM focuses on narrative paragraphs when designing the ontology — table columns are invisible signal.

Result: `HOUSING_SIZE_VARIANT` entity type exists, but `HAS_RATED_AIRFLOW`, `HAS_HOUSING_WEIGHT`, `COMPOSED_OF_MODULES` do not. These predicates are only invented later by the enrichment step (during Q&A), and then silently dropped because they're not in the ontology.

**Two gaps in init:**
1. **Ontology gap** — column headers not mapped to relationship type candidates
2. **Structure gap** — bootstrap doesn't distinguish: which column is the primary key (entity text), which are measurable dimensions (relationship types), which are categorical (properties or entity types)

### Fix: `extract_table_schemas()` + TABLE SCHEMAS section in discovery prompt

#### Step 1 — extract_table_schemas() in pipeline.py

```python
# In src/synapse/bootstrap/pipeline.py

from synapse.extraction.tables import parse_md_tables

def extract_table_schemas(sample_text: str) -> str:
    """Extract table structures from sample page markdown.
    
    Returns a formatted TABLE SCHEMAS summary for injection into
    the ontology discovery prompt.
    """
    import re
    
    # Split sample_text into per-page blocks
    pages = re.split(r'<page_\d+>', sample_text)
    
    seen_headers: dict[str, int] = {}  # header_signature → row_count
    
    for page_text in pages:
        tables = parse_md_tables(page_text)
        for t in tables:
            if len(t["rows"]) < 2:
                continue
            # Signature = tuple of column names
            sig = " | ".join(t["columns"])
            seen_headers[sig] = seen_headers.get(sig, 0) + len(t["rows"])
    
    if not seen_headers:
        return ""
    
    lines = ["TABLE SCHEMAS FOUND IN DOCUMENTS:"]
    lines.append("(Each column is a potential property or relationship type)")
    lines.append("")
    
    for sig, row_count in sorted(seen_headers.items(), key=lambda x: -x[1]):
        cols = sig.split(" | ")
        lines.append(f"Table ({row_count} rows): {sig}")
        # Classify columns heuristically
        for col in cols:
            col_lower = col.lower()
            if any(k in col_lower for k in ("vikt", "weight", "kg", "g ", "flöde", "flow", "m³/h", "bredd", "höjd", "width", "height", "length", "längd")):
                lines.append(f"  → NUMERIC: '{col}' → candidate relationship type, e.g. HAS_{col.upper().replace(' ', '_').replace('/', '_')}")
            elif any(k in col_lower for k in ("storlek", "size", "typ", "type", "modul", "material", "klass", "class")):
                lines.append(f"  → IDENTIFIER/CATEGORY: '{col}' → likely entity text or categorical property")
    
    lines.append("")
    lines.append("INSTRUCTION: For EACH numeric column above, define a corresponding relationship type")
    lines.append("(e.g. HAS_WEIGHT_KG, HAS_RATED_AIRFLOW, HAS_WIDTH_MM). For identifier columns,")
    lines.append("ensure the entity type description includes them in the entity text (not just properties).")
    
    return "\n".join(lines)
```

#### Step 2 — Inject into discover_ontology()

```python
# In bootstrap() function, before calling discover_ontology():

table_schemas = extract_table_schemas(sample_text)

ontology = await discover_ontology(
    sample_text=sample_text,
    table_schemas=table_schemas,   # NEW
    domain_context=domain_context,
    llm=llm,
    ...
)
```

#### Step 3 — Add TABLE SCHEMAS to ONTOLOGY_DISCOVERY_USER prompt

```python
# In prompts.py — add to ONTOLOGY_DISCOVERY_USER, after SAMPLE PAGES block:

{table_schemas}

# And add to GUIDELINES:
# - CRITICAL for tabular documents: use the TABLE SCHEMAS section above.
#   For each NUMERIC column header, create a corresponding relationship type
#   (e.g. "Vikt kg 850/900" → HAS_WEIGHT_850_900, "Flöde m³/h" → HAS_RATED_AIRFLOW).
#   For IDENTIFIER columns ("Storlek", "Size"), ensure the entity type description
#   mandates including that column value in the entity text (not just in properties).
#   This is the only way multi-row table data is queryable in the knowledge graph.
```

#### Step 4 — Table-aware ONTOLOGY_REFINEMENT_USER

Add a check in refinement:
```
10. Table completeness check: if TABLE SCHEMAS were provided, verify that each
    numeric column has a corresponding relationship type in the ontology. Add any
    missing ones. Ensure entity type descriptions for table row entities mandate
    including the primary key column in entity text.
```

### Expected result

After this fix, `synapse init` on `filter_housings_sweden.pdf` would generate:

```json
"relationship_types": {
  "HAS_RATED_AIRFLOW": "Relates a housing size variant to its rated airflow in m³/h at standard conditions",
  "HAS_WEIGHT_850_900": "Weight in kg for the 850/900mm length variant",
  "HAS_WEIGHT_600_650": "Weight in kg for the 600/650mm length variant",
  "HAS_WIDTH_MM": "Housing width dimension in mm",
  "HAS_HEIGHT_MM": "Housing height dimension in mm",
  "COMPOSED_OF_MODULES": "Module configuration (1/4, 1/2, 1/1) of a housing size variant",
  ...
}
```

These types would then be available to both extraction (entities stored with correct properties) and enrichment (Q&A findings stored correctly), closing the loop.

---

## Phase 8: TABLE_QUERY Tool + Re-ingest MÅTT Pages

**Priority:** High  
**Impact:** High — expected +20pp on QA benchmark for table questions  
**Depends on:** Phase 1.1 (deterministic table extraction already done in `tables.py`)

### 8.1 Re-ingest all MÅTT pages with deterministic strategy

Run extraction on pages 6, 9, 12, 14, 16, 18, 20, 21, 22 using `table_strategy=deterministic`. Expected: ~225 new size-variant entities + population of `extracted_table_rows` SQLite store.

### 8.2 Implement TABLE_QUERY tool

See Phase 7.4 above. The `extracted_table_rows` store is already written by `process_section_tables()` — only the reasoning agent tool is missing.

### 8.3 Success metric

Re-run QA benchmark after Phase 7 fixes + Phase 8:

| Metric | Current (mnh-det) | Target |
|--------|-------------------|--------|
| QA score | 18/40 (45%) | > 30/40 (75%) |
| Runaway questions | 4/20 | 0/20 |
| table_lookup accuracy | 67% | > 90% |
| table_filter accuracy | 17% | > 60% |
| table_aggregation accuracy | 67% | > 80% |

---

# TODO
Zapipsywanie sposób dochodzenia do rozwiązania jako cache. Zapisać patterny, żeby później LLM nie tworzył koła od nowa, tylko odpytywał konkretne rzeczy, żeby dość do rozwiązania.
