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
