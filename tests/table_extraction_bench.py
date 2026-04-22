"""Benchmark 5 table extraction strategies on GDMI pages 11-12.

Usage:
    python tests/table_extraction_bench.py

Strategies:
1. Deterministic parse — zero LLM, regex → structured entities
2. LLM per-row — parse columns, LLM classifies one row at a time
3. LLM schema-first — LLM designs mapping from header, then deterministic parse
4. LLM full table — send full table to LLM with aggressive prompt
5. Hybrid: deterministic parse + LLM enrichment (relations, context)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from synapse.config import get_settings
from synapse.llm.client import LLMClient

# ── Load test data ───────────────────────────────────────────

PAGES_DIR = Path(__file__).resolve().parent.parent.parent / "docs-test" / "filter_housings_sweden" / "pages"
PAGE_11 = (PAGES_DIR / "page-11" / "markdown.md").read_text()
PAGE_12 = (PAGES_DIR / "page-12" / "markdown.md").read_text()
COMBINED = PAGE_11 + "\n\n" + PAGE_12

EXPECTED_ROWS = 44  # page-12 has 44 data rows


# ── Helpers ──────────────────────────────────────────────────

def parse_md_table(text: str) -> list[dict]:
    """Parse ALL markdown tables in text. Returns list of {columns, rows}."""
    tables = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if re.match(r"^\|.+\|$", line):
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
                rows = []
                j = i + 2
                while j < len(lines):
                    row = lines[j].strip()
                    if re.match(r"^\|.+\|$", row) and not re.match(r"^\|[\s\-:|]+\|$", row):
                        vals = [c.strip() for c in row.split("|") if c.strip() or row.count("|") > 1]
                        # handle pipes properly
                        raw_parts = row.split("|")
                        vals = [p.strip() for p in raw_parts[1:-1]]  # skip first/last empty
                        rows.append(vals)
                        j += 1
                    else:
                        break
                if rows:
                    tables.append({"columns": cols, "rows": rows})
                i = j
                continue
        i += 1
    return tables


def col_to_key(name: str) -> str:
    """Column name → snake_case key."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "col"


def get_llm() -> LLMClient:
    s = get_settings()
    model = s.extraction_model or s.llm_model
    return LLMClient(api_key=s.llm_api_key, base_url=s.llm_base_url, model=model, timeout=s.llm_timeout)


def print_result(name: str, entities: list[dict], rels: list[dict], elapsed: float):
    print(f"\n{'='*60}")
    print(f"Strategy: {name}")
    print(f"{'='*60}")
    print(f"  Entities: {len(entities)}")
    print(f"  Relationships: {len(rels)}")
    print(f"  Time: {elapsed:.1f}s")
    if entities:
        types = {}
        for e in entities:
            t = e.get("entity_type", "?")
            types[t] = types.get(t, 0) + 1
        print(f"  Entity types: {dict(sorted(types.items(), key=lambda x: -x[1]))}")
        print(f"  Sample entity: {json.dumps(entities[0], ensure_ascii=False)[:200]}")
    if rels:
        print(f"  Sample rel: {json.dumps(rels[0], ensure_ascii=False)[:200]}")


# ── Strategy 1: Deterministic Parse ──────────────────────────

def strategy_deterministic(text: str) -> tuple[list[dict], list[dict]]:
    """Zero LLM. Parse markdown tables into structured entities."""
    tables = parse_md_table(text)
    entities = []
    rels = []

    # Extract context from non-table text
    title_match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    product_prefix = ""
    if title_match:
        # "GDMI — MODULFILTERSKÅP" → "GDMI"
        product_prefix = title_match.group(1).split("—")[0].split("–")[0].strip()

    for tbl in tables:
        cols = tbl["columns"]
        for row_vals in tbl["rows"]:
            props = {}
            for col, val in zip(cols, row_vals):
                key = col_to_key(col)
                if val.strip():
                    props[key] = val.strip()

            # Build entity text from first column + prefix
            first_val = row_vals[0].strip() if row_vals else ""
            entity_text = f"{product_prefix} {first_val}".strip() if product_prefix else first_val

            # Guess entity type from context
            entity_type = "TABLE_ROW"
            if any(k in " ".join(cols).lower() for k in ["storlek", "size", "mått", "bredd", "höjd"]):
                entity_type = "HOUSING_SIZE_VARIANT"
            elif any(k in " ".join(cols).lower() for k in ["typ", "type"]):
                entity_type = "PRODUCT_VARIANT"

            if entity_text:
                entities.append({
                    "text": entity_text,
                    "entity_type": entity_type,
                    "confidence": 1.0,
                    "properties": props,
                })

            # Create relationship to parent product
            if product_prefix and entity_text:
                rels.append({
                    "subject": product_prefix,
                    "predicate": "HAS_SIZE_VARIANT",
                    "object": entity_text,
                })

    return entities, rels


# ── Strategy 2: LLM Per-Row ─────────────────────────────────

async def strategy_llm_per_row(text: str, llm: LLMClient) -> tuple[list[dict], list[dict]]:
    """Parse columns deterministically, LLM classifies one row at a time."""
    tables = parse_md_table(text)
    entities = []
    rels = []

    for tbl in tables:
        cols = tbl["columns"]
        if len(tbl["rows"]) < 3:
            continue

        # Send first 3 rows to LLM to establish pattern
        sample_rows = tbl["rows"][:3]
        sample_text = "\n".join(
            " | ".join(f"{c}: {v}" for c, v in zip(cols, row))
            for row in sample_rows
        )

        result = await llm.complete_json_lenient(
            system="You are a data extraction expert. Classify table rows into knowledge graph entities.",
            user=f"""These rows come from a technical product specification table.
Columns: {', '.join(cols)}

Sample rows:
{sample_text}

For these rows, determine:
1. entity_type — what type of entity each row represents (e.g., HOUSING_SIZE_VARIANT, FILTER_PRODUCT, PRODUCT_SPECIFICATION)
2. text_template — how to construct the entity name from columns (e.g., "GDMI {{col1}} — {{col4}} m³/h")
3. key_columns — which columns form the identifying name
4. property_columns — which columns map to properties

Return JSON: {{"entity_type": "...", "text_template": "...", "key_columns": [...], "property_columns": [...]}}""",
            max_tokens=1024,
        )

        if not isinstance(result, dict) or "entity_type" not in result:
            continue

        etype = result.get("entity_type", "TABLE_ROW")

        # Apply pattern to all rows
        for row_vals in tbl["rows"]:
            props = {}
            for col, val in zip(cols, row_vals):
                if val.strip():
                    props[col_to_key(col)] = val.strip()

            # Build text from key columns
            key_parts = []
            for kc in result.get("key_columns", [cols[0]]):
                idx = next((i for i, c in enumerate(cols) if c == kc), 0)
                if idx < len(row_vals) and row_vals[idx].strip():
                    key_parts.append(row_vals[idx].strip())
            entity_text = " ".join(key_parts) if key_parts else row_vals[0].strip()

            if entity_text:
                entities.append({
                    "text": entity_text,
                    "entity_type": etype,
                    "confidence": 0.95,
                    "properties": props,
                })

    return entities, rels


# ── Strategy 3: LLM Schema-First ────────────────────────────

async def strategy_llm_schema_first(text: str, llm: LLMClient) -> tuple[list[dict], list[dict]]:
    """LLM analyzes header + context → generates full mapping, then deterministic apply."""
    tables = parse_md_table(text)
    entities = []
    rels = []

    # Get non-table context
    context_lines = [l for l in text.splitlines() if not re.match(r"^\|", l.strip())]
    context = "\n".join(context_lines[:20])

    for tbl in tables:
        cols = tbl["columns"]
        if len(tbl["rows"]) < 3:
            continue

        first_row = " | ".join(f"{c}: {v}" for c, v in zip(cols, tbl["rows"][0]))

        result = await llm.complete_json_lenient(
            system="You design entity extraction schemas for knowledge graphs.",
            user=f"""Given this table from a technical document, design an extraction schema.

Document context:
{context}

Table columns: {', '.join(cols)}
First row: {first_row}
Total rows: {len(tbl['rows'])}

Design a schema for converting each row into a knowledge graph entity:

Return JSON:
{{
  "entity_type": "the entity type name (e.g., HOUSING_SIZE_VARIANT)",
  "text_format": "how to build entity text, use {{column_name}} placeholders",
  "relationships": [
    {{"predicate": "REL_NAME", "object_type": "TYPE", "object_source": "column_name or fixed value"}}
  ],
  "column_mapping": {{
    "column_name": {{"property_key": "snake_case_key", "data_type": "string|number|boolean"}},
    ...
  }}
}}""",
            max_tokens=2048,
        )

        if not isinstance(result, dict):
            continue

        etype = result.get("entity_type", "TABLE_ROW")
        text_fmt = result.get("text_format", "{" + cols[0] + "}")
        col_map = result.get("column_mapping", {})
        rel_templates = result.get("relationships", [])

        for row_vals in tbl["rows"]:
            row_dict = {c: v.strip() for c, v in zip(cols, row_vals)}

            # Build text
            entity_text = text_fmt
            for c, v in row_dict.items():
                entity_text = entity_text.replace("{" + c + "}", v)
                entity_text = entity_text.replace("{{" + c + "}}", v)

            # Build properties
            props = {}
            for c, v in row_dict.items():
                if not v:
                    continue
                mapping = col_map.get(c, {})
                key = mapping.get("property_key", col_to_key(c))
                dtype = mapping.get("data_type", "string")
                if dtype == "number":
                    try:
                        props[key] = float(v) if "." in v else int(v)
                    except ValueError:
                        props[key] = v
                else:
                    props[key] = v

            if entity_text.strip():
                entities.append({
                    "text": entity_text.strip(),
                    "entity_type": etype,
                    "confidence": 0.95,
                    "properties": props,
                })

            # Build relationships from templates
            for rt in rel_templates:
                obj_src = rt.get("object_source", "")
                obj_val = row_dict.get(obj_src, obj_src)  # column value or literal
                if obj_val:
                    rels.append({
                        "subject": entity_text.strip(),
                        "predicate": rt.get("predicate", "RELATES_TO"),
                        "object": obj_val,
                    })

    return entities, rels


# ── Strategy 4: LLM Full Table ───────────────────────────────

async def strategy_llm_full_table(text: str, llm: LLMClient) -> tuple[list[dict], list[dict]]:
    """Send the full table to LLM with aggressive structured prompt."""
    tables = parse_md_table(text)
    entities = []
    rels = []

    for tbl in tables:
        if len(tbl["rows"]) < 3:
            continue

        cols = tbl["columns"]
        # Build markdown table text
        header = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        rows_text = "\n".join(
            "| " + " | ".join(vals) + " |"
            for vals in tbl["rows"]
        )
        table_md = f"{header}\n{sep}\n{rows_text}"

        # Get non-table context
        context_lines = [l for l in text.splitlines() if not re.match(r"^\|", l.strip())]
        context = "\n".join(context_lines[:15])

        # Send with aggressive prompt
        result = await llm.complete_json_lenient(
            system="You extract structured entities from technical specification tables for knowledge graphs. Return a JSON array.",
            user=f"""Document context:
{context}

Table ({len(tbl['rows'])} rows):
{table_md}

Extract EVERY row as a separate entity. Return JSON array:
[{{"text": "descriptive name from key columns", "entity_type": "HOUSING_SIZE_VARIANT", "confidence": 0.95, "properties": {{...all columns mapped...}}}}]

CRITICAL: You MUST return exactly {len(tbl['rows'])} entities — one per table row. Do NOT skip, summarize, or sample.

Column → property key mapping:
{chr(10).join(f'  {c} → {col_to_key(c)}' for c in cols)}

For the "text" field, combine the product name from context with the first column value.""",
            max_tokens=min(16384, 8192 + len(tbl["rows"]) * 200),
        )

        if isinstance(result, dict):
            for key in ("entities", "data", "result"):
                if key in result and isinstance(result[key], list):
                    result = result[key]
                    break
            else:
                if "text" in result:
                    result = [result]
                else:
                    result = []

        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and item.get("text"):
                    entities.append(item)

    return entities, rels


# ── Strategy 5: Hybrid (deterministic + LLM enrich) ─────────

async def strategy_hybrid(text: str, llm: LLMClient) -> tuple[list[dict], list[dict]]:
    """Deterministic parse → entities, then LLM adds relationships and context."""
    # Step 1: deterministic parse (100% coverage guaranteed)
    entities, _ = strategy_deterministic(text)

    if not entities:
        return entities, []

    # Step 2: LLM enrichment — infer relationships and improve entity types
    entity_summary = "\n".join(
        f"- {e['text']} ({e['entity_type']}): {json.dumps({k: v for k, v in list(e['properties'].items())[:4]}, ensure_ascii=False)}"
        for e in entities[:15]
    )

    context_lines = [l for l in text.splitlines() if not re.match(r"^\|", l.strip())]
    context = "\n".join(context_lines[:20])

    result = await llm.complete_json_lenient(
        system="You are a knowledge graph expert. Given pre-extracted entities from a technical table, infer relationships and improve entity classifications.",
        user=f"""Document context:
{context}

{len(entities)} entities were extracted from tables (showing first 15):
{entity_summary}

Tasks:
1. Confirm or improve entity_type for these entities
2. Identify relationships between entities and any products/concepts mentioned in context
3. Identify the parent product these size variants belong to

Return JSON:
{{
  "entity_type": "the correct type (or keep as-is)",
  "parent_product": "name of the parent product/housing type",
  "relationships": [
    {{"subject_pattern": "description of which entities", "predicate": "REL_NAME", "object": "target entity or value"}}
  ],
  "property_enrichments": {{
    "new_property_key": "description of value to add to all entities"
  }}
}}""",
        max_tokens=2048,
    )

    rels = []
    if isinstance(result, dict):
        # Update entity types if LLM suggests improvement
        new_type = result.get("entity_type")
        if new_type and isinstance(new_type, str):
            for e in entities:
                e["entity_type"] = new_type

        # Add parent relationships
        parent = result.get("parent_product", "")
        if parent:
            for e in entities:
                rels.append({
                    "subject": parent,
                    "predicate": "HAS_SIZE_VARIANT",
                    "object": e["text"],
                })

        # Add enrichments
        enrichments = result.get("property_enrichments", {})
        if isinstance(enrichments, dict):
            for e in entities:
                e["properties"].update(enrichments)

    return entities, rels


# ── Main ─────────────────────────────────────────────────────

async def main():
    llm = get_llm()
    print(f"Test data: page-11 ({len(PAGE_11)} chars) + page-12 ({len(PAGE_12)} chars)")
    print(f"Expected table rows from page-12: {EXPECTED_ROWS}")
    print(f"LLM model: {llm.model}")

    results = {}

    # Strategy 1: Deterministic
    t0 = time.time()
    ents, rels = strategy_deterministic(COMBINED)
    elapsed = time.time() - t0
    print_result("1. Deterministic Parse", ents, rels, elapsed)
    results["1_deterministic"] = {"entities": len(ents), "rels": len(rels), "time": elapsed}

    # Strategy 2: LLM Per-Row
    t0 = time.time()
    ents, rels = await strategy_llm_per_row(COMBINED, llm)
    elapsed = time.time() - t0
    print_result("2. LLM Per-Row", ents, rels, elapsed)
    results["2_llm_per_row"] = {"entities": len(ents), "rels": len(rels), "time": elapsed}

    # Strategy 3: LLM Schema-First
    t0 = time.time()
    ents, rels = await strategy_llm_schema_first(COMBINED, llm)
    elapsed = time.time() - t0
    print_result("3. LLM Schema-First", ents, rels, elapsed)
    results["3_llm_schema_first"] = {"entities": len(ents), "rels": len(rels), "time": elapsed}

    # Strategy 4: LLM Full Table
    t0 = time.time()
    ents, rels = await strategy_llm_full_table(COMBINED, llm)
    elapsed = time.time() - t0
    print_result("4. LLM Full Table", ents, rels, elapsed)
    results["4_llm_full_table"] = {"entities": len(ents), "rels": len(rels), "time": elapsed}

    # Strategy 5: Hybrid
    t0 = time.time()
    ents, rels = await strategy_hybrid(COMBINED, llm)
    elapsed = time.time() - t0
    print_result("5. Hybrid (Parse + LLM Enrich)", ents, rels, elapsed)
    results["5_hybrid"] = {"entities": len(ents), "rels": len(rels), "time": elapsed}

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Strategy':<30} {'Entities':>8} {'Rels':>8} {'Time':>8} {'Coverage':>10}")
    print("-" * 70)
    for name, r in results.items():
        coverage = f"{r['entities'] / EXPECTED_ROWS * 100:.0f}%" if EXPECTED_ROWS else "?"
        print(f"{name:<30} {r['entities']:>8} {r['rels']:>8} {r['time']:>7.1f}s {coverage:>10}")


if __name__ == "__main__":
    asyncio.run(main())
