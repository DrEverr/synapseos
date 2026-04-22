"""Bootstrap pipeline — discover ontology from documents, generate prompts, store in SQLite.

Pipeline:
1. Sample pages from the provided document batch
2. Analyze domain (what field are these documents from?)
3. Discover ontology (entity types + relationship types) from sampled content
4. Refine the ontology (merge, deduplicate, validate)
5. Generate domain-specific prompts (entity extraction, relationship extraction, reasoning, etc.)
6. Detect boilerplate keywords for this domain
7. Store everything in the InstanceStore (SQLite)
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from synapse.bootstrap.prompts import (
    BOILERPLATE_KEYWORDS_DISCOVERY_SYSTEM,
    BOILERPLATE_KEYWORDS_DISCOVERY_USER,
    CONTRADICTION_SYSTEM,
    CONTRADICTION_USER,
    DOMAIN_ANALYSIS_SYSTEM,
    DOMAIN_ANALYSIS_USER,
    DOMAIN_KNOWLEDGE_GENERATION_SYSTEM,
    DOMAIN_KNOWLEDGE_GENERATION_USER,
    ONTOLOGY_DISCOVERY_INCREMENTAL_USER,
    ONTOLOGY_DISCOVERY_SYSTEM,
    ONTOLOGY_DISCOVERY_USER,
    ONTOLOGY_REFINEMENT_SYSTEM,
    ONTOLOGY_REFINEMENT_USER,
    PROMPT_GENERATION_SYSTEM,
    PROMPT_GENERATION_USER,
)
from synapse.config import Settings
from synapse.extraction.tables import parse_md_tables
from synapse.llm.client import LLMClient
from synapse.parsers import extract_pages
from synapse.parsers.pdf import pages_to_tagged_text
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)


def _sample_pages(all_pages: list[list[str]], max_pages: int = 30) -> str:
    """Sample representative pages from a batch of documents.

    Strategy: take first 2 pages (usually TOC/intro), last page,
    and random middle pages from each document until we hit max_pages.
    """
    sampled: list[tuple[str, int, str]] = []  # (filename, page_num, text)

    for doc_pages in all_pages:
        if not doc_pages:
            continue
        n = len(doc_pages)
        # Always include first 2 pages and last page
        indices = {0, min(1, n - 1), n - 1}
        # Add random middle pages
        middle = list(range(2, max(2, n - 1)))
        if middle:
            sample_count = min(len(middle), max(3, max_pages // len(all_pages)))
            indices.update(random.sample(middle, min(sample_count, len(middle))))
        for idx in sorted(indices):
            if len(sampled) >= max_pages:
                break
            sampled.append(("doc", idx + 1, doc_pages[idx]))

    # Format as tagged text
    parts = []
    for _, page_num, text in sampled[:max_pages]:
        text = text.strip()
        if text:
            parts.append(f"<page_{page_num}>{text}</page_{page_num}>")
    return "\n\n".join(parts)


_NUMERIC_HINTS = (
    "weight", "kg", "flow", "m³/h", "m3/h", "width", "height", "length",
    "depth", "mm", "cm", "capacity", "pressure", "pa", "kpa", "area",
    "m²", "m2", "count", "quantity", "volume", "speed", "power", "kw",
    "temperature", "°c", "rating", "score", "cost", "price",
)
_IDENTIFIER_HINTS = (
    "size", "type", "module", "material", "class", "designation",
    "article", "model", "name", "variant", "category", "code", "id",
    "label", "grade", "series", "version",
)


def extract_table_schemas(sample_text: str) -> str:
    """Extract table column structures from sample pages.

    Parses markdown tables from the sampled text and builds a TABLE SCHEMAS
    summary that tells the ontology discovery LLM which columns exist and
    whether they are numeric dimensions or identifiers.
    """
    import re as _re

    # Split by page tags to get individual page texts
    page_blocks = _re.split(r"</?page_\d+>", sample_text)

    schemas: list[tuple[list[str], int]] = []  # (columns, total_rows)
    seen_sigs: dict[str, int] = {}  # col_signature → index in schemas

    for block in page_blocks:
        tables = parse_md_tables(block)
        for t in tables:
            if len(t["rows"]) < 2:
                continue
            sig = "|".join(c.lower().strip() for c in t["columns"])
            if sig in seen_sigs:
                idx = seen_sigs[sig]
                old_cols, old_rows = schemas[idx]
                schemas[idx] = (old_cols, old_rows + len(t["rows"]))
            else:
                seen_sigs[sig] = len(schemas)
                schemas.append((t["columns"], len(t["rows"])))

    if not schemas:
        return ""

    lines = [
        "TABLE SCHEMAS FOUND IN DOCUMENTS:",
        "Each column header below is a candidate for a property or relationship type.",
        "",
    ]

    for cols, row_count in sorted(schemas, key=lambda x: -x[1]):
        lines.append(f"Table ({row_count} data rows):")
        lines.append(f"  Columns: {' | '.join(cols)}")
        for col in cols:
            col_lower = col.lower()
            if any(h in col_lower for h in _NUMERIC_HINTS):
                lines.append(f"    → NUMERIC: '{col}' — candidate for a HAS_* relationship type")
            elif any(h in col_lower for h in _IDENTIFIER_HINTS):
                lines.append(f"    → IDENTIFIER: '{col}' — should be part of entity text")
        lines.append("")

    lines.append(
        "For each NUMERIC column, create a relationship type (e.g. HAS_WEIGHT, "
        "HAS_RATED_AIRFLOW). For IDENTIFIER columns, ensure the entity type "
        "description mandates including that value in the entity text."
    )
    return "\n".join(lines)


async def analyze_domain(
    sample_text: str,
    llm: LLMClient,
) -> dict:
    """Step 1: Identify the domain, language, and key topics from sample pages."""
    result = await llm.complete_json_lenient(
        system=DOMAIN_ANALYSIS_SYSTEM,
        user=DOMAIN_ANALYSIS_USER.format(sample_text=sample_text),
    )
    if not isinstance(result, dict):
        raise ValueError(f"Domain analysis returned non-dict: {type(result)}")
    logger.info("Domain analysis: %s / %s", result.get("domain"), result.get("subdomain"))
    return result


async def discover_ontology(
    sample_text: str,
    domain_context: str,
    llm: LLMClient,
    max_entity_types: int = 15,
    max_rel_types: int = 20,
    existing_entity_types: dict[str, str] | None = None,
    existing_relationship_types: dict[str, str] | None = None,
    table_schemas: str = "",
) -> dict:
    """Step 2: Discover entity types and relationship types from document samples."""
    if existing_entity_types:
        # Incremental discovery
        user = ONTOLOGY_DISCOVERY_INCREMENTAL_USER.format(
            domain_context=domain_context,
            existing_entity_types=json.dumps(existing_entity_types, indent=2),
            existing_relationship_types=json.dumps(existing_relationship_types or {}, indent=2),
            sample_text=sample_text,
            table_schemas=table_schemas,
            max_entity_types=max_entity_types,
            max_rel_types=max_rel_types,
        )
    else:
        user = ONTOLOGY_DISCOVERY_USER.format(
            domain_context=domain_context,
            sample_text=sample_text,
            table_schemas=table_schemas,
            max_entity_types=max_entity_types,
            max_rel_types=max_rel_types,
        )

    result = await llm.complete_json_lenient(
        system=ONTOLOGY_DISCOVERY_SYSTEM,
        user=user,
    )
    if not isinstance(result, dict):
        raise ValueError(f"Ontology discovery returned non-dict: {type(result)}")

    etypes = result.get("entity_types", {})
    rtypes = result.get("relationship_types", {})
    logger.info("Discovered %d entity types, %d relationship types", len(etypes), len(rtypes))
    return result


async def refine_ontology(
    entity_types: dict[str, str],
    relationship_types: dict[str, str],
    domain: str,
    key_topics: list[str],
    llm: LLMClient,
    max_entity_types: int = 15,
    max_rel_types: int = 20,
    table_schemas: str = "",
) -> dict:
    """Step 3: Refine and finalize the discovered ontology."""
    result = await llm.complete_json_lenient(
        system=ONTOLOGY_REFINEMENT_SYSTEM,
        user=ONTOLOGY_REFINEMENT_USER.format(
            domain=domain,
            entity_types=json.dumps(entity_types, indent=2),
            relationship_types=json.dumps(relationship_types, indent=2),
            key_topics=", ".join(key_topics),
            table_schemas=table_schemas,
            max_entity_types=max_entity_types,
            max_rel_types=max_rel_types,
        ),
    )
    if not isinstance(result, dict):
        raise ValueError(f"Ontology refinement returned non-dict: {type(result)}")

    changes = result.get("changes_made", [])
    if changes:
        logger.info("Ontology refinement changes: %s", "; ".join(changes[:5]))
    return result


async def generate_domain_knowledge_context(
    sample_text: str,
    domain: str,
    subdomain: str,
    language: str,
    key_topics: list[str],
    llm: LLMClient,
) -> str:
    """Generate a domain knowledge context from sample pages.

    Returns plain-text summary of cross-document domain knowledge
    (terminology, standards, conventions, naming patterns).
    """
    result = await llm.complete(
        system=DOMAIN_KNOWLEDGE_GENERATION_SYSTEM,
        user=DOMAIN_KNOWLEDGE_GENERATION_USER.format(
            domain=domain,
            subdomain=subdomain,
            language=language,
            key_topics=", ".join(key_topics),
            sample_text=sample_text,
        ),
        max_tokens=4096,
    )
    text = result.strip()
    # If LLM wrapped the response in JSON, extract the string
    if text.startswith(("{", '"')):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, str):
                text = parsed
            elif isinstance(parsed, dict):
                for key in ("context", "domain_knowledge", "text", "result"):
                    if key in parsed and isinstance(parsed[key], str):
                        text = parsed[key]
                        break
        except json.JSONDecodeError:
            pass
    logger.info("Generated domain knowledge context (%d chars)", len(text))
    return text


async def generate_prompts(
    entity_types: dict[str, str],
    relationship_types: dict[str, str],
    domain: str,
    language: str,
    document_types: list[str],
    key_topics: list[str],
    llm: LLMClient,
) -> dict[str, str]:
    """Step 4: Generate all domain-specific prompts."""
    result = await llm.complete_json_lenient(
        system=PROMPT_GENERATION_SYSTEM,
        user=PROMPT_GENERATION_USER.format(
            domain=domain,
            entity_types=json.dumps(entity_types, indent=2),
            relationship_types=json.dumps(relationship_types, indent=2),
            language=language,
            document_types=", ".join(document_types),
            key_topics=", ".join(key_topics),
        ),
        max_tokens=8192,
    )
    if not isinstance(result, dict):
        raise ValueError(f"Prompt generation returned non-dict: {type(result)}")

    expected_keys = [
        "entity_extraction_system",
        "entity_extraction_user",
        "relationship_extraction_system",
        "relationship_extraction_user",
        "reasoning_system",
        "reasoning_user",
        "tree_search_system",
        "tree_search_user",
        "enrichment_system",
        "enrichment_user",
    ]
    missing = [k for k in expected_keys if k not in result]
    if missing:
        logger.warning("Prompt generation missing keys: %s", missing)

    logger.info("Generated %d prompts", len(result))
    return {k: str(v) for k, v in result.items()}


async def detect_contradiction_pairs(
    relationship_types: dict[str, str],
    llm: LLMClient,
) -> list[list[str]]:
    """Detect contradictory relationship pairs from the ontology using LLM."""
    rel_list = "\n".join(f"- {k}: {v}" for k, v in sorted(relationship_types.items()))
    try:
        result = await llm.complete_json_lenient(
            system=CONTRADICTION_SYSTEM,
            user=CONTRADICTION_USER.format(relationship_types=rel_list),
            max_tokens=1024,
        )
        if isinstance(result, list):
            pairs = [[str(p[0]), str(p[1])] for p in result if isinstance(p, list) and len(p) == 2]
            logger.info("Detected %d contradiction pair(s)", len(pairs))
            return pairs
        return []
    except Exception as e:
        logger.warning("Contradiction pair detection failed: %s", e)
        return []


async def discover_boilerplate_keywords(
    domain: str,
    document_types: list[str],
    llm: LLMClient,
) -> list[str]:
    """Step 5: Discover boilerplate keywords for this domain."""
    try:
        result = await llm.complete_json_lenient(
            system=BOILERPLATE_KEYWORDS_DISCOVERY_SYSTEM,
            user=BOILERPLATE_KEYWORDS_DISCOVERY_USER.format(
                domain=domain,
                document_types=", ".join(document_types),
            ),
        )
        if isinstance(result, list):
            return [str(kw).lower() for kw in result]
        if isinstance(result, dict):
            for key in ("keywords", "boilerplate", "data"):
                if key in result and isinstance(result[key], list):
                    return [str(kw).lower() for kw in result[key]]
        return []
    except Exception as e:
        logger.warning("Boilerplate keyword discovery failed: %s", e)
        return ["legal", "disclaimer", "copyright", "trademark", "index", "bibliography"]


async def bootstrap(
    pdf_paths: list[str],
    settings: Settings,
    store: InstanceStore,
) -> dict:
    """Run the full bootstrap pipeline.

    Takes a batch of PDF documents, discovers the domain ontology,
    generates all extraction/reasoning prompts, and stores everything
    in the SQLite instance store.

    Returns a summary dict.
    """
    model = settings.bootstrap_model or settings.llm_model
    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model,
        timeout=settings.llm_timeout,
    )
    logger.info("Bootstrap using model: %s", model)

    # ── 1. Extract pages from all documents ───────────────────
    all_pages: list[list[str]] = []
    for pdf_path in pdf_paths:
        try:
            pages = extract_pages(pdf_path)
            all_pages.append(pages)
            logger.info("Extracted %d pages from %s", len(pages), Path(pdf_path).name)
        except Exception as e:
            logger.error("Failed to extract pages from %s: %s", pdf_path, e)

    if not all_pages:
        raise ValueError("No pages extracted from any document")

    total_pages = sum(len(p) for p in all_pages)
    logger.info("Total pages across %d documents: %d", len(all_pages), total_pages)

    # ── 2. Sample pages for analysis ──────────────────────────
    sample_text = _sample_pages(all_pages, max_pages=settings.bootstrap_sample_pages)

    # ── 3. Analyze domain ─────────────────────────────────────
    logger.info("Step 1/6: Analyzing domain...")
    domain_info = await analyze_domain(sample_text, llm)
    domain = domain_info.get("domain", "general")
    subdomain = domain_info.get("subdomain", "")
    language = domain_info.get("language", "English")
    document_types = domain_info.get("document_types", [])
    key_topics = domain_info.get("key_topics", [])
    scientific_aspects = domain_info.get("scientific_aspects", [])
    domain_context = (
        f"Domain: {domain}. Subdomain: {subdomain}. Language: {language}. "
        f"Topics: {', '.join(key_topics)}."
        + (f" Underlying scientific aspects: {', '.join(scientific_aspects)}." if scientific_aspects else "")
    )

    # ── 3b. Extract table schemas from sampled pages ────────────
    table_schemas = extract_table_schemas(sample_text)
    if table_schemas:
        logger.info("Found table schemas in sample pages — will inform ontology discovery")

    # ── 4. Discover ontology (may run multiple rounds for large batches) ─
    logger.info("Step 2/6: Discovering ontology...")

    # First round with initial sample
    ontology = await discover_ontology(
        sample_text=sample_text,
        domain_context=domain_context,
        llm=llm,
        max_entity_types=settings.bootstrap_max_entity_types,
        max_rel_types=settings.bootstrap_max_rel_types,
        table_schemas=table_schemas,
    )
    entity_types = ontology.get("entity_types", {})
    relationship_types = ontology.get("relationship_types", {})

    # Additional round(s) if we have many documents
    if len(all_pages) > 3:
        # Sample different pages for a second pass
        random.seed(42)  # Reproducible but different from first sample
        second_sample = _sample_pages(all_pages, max_pages=settings.bootstrap_sample_pages)
        second_table_schemas = extract_table_schemas(second_sample)
        ontology2 = await discover_ontology(
            sample_text=second_sample,
            domain_context=domain_context,
            llm=llm,
            max_entity_types=settings.bootstrap_max_entity_types,
            max_rel_types=settings.bootstrap_max_rel_types,
            existing_entity_types=entity_types,
            existing_relationship_types=relationship_types,
            table_schemas=second_table_schemas or table_schemas,
        )
        entity_types = ontology2.get("entity_types", entity_types)
        relationship_types = ontology2.get("relationship_types", relationship_types)

    # ── 5. Refine ontology ────────────────────────────────────
    logger.info("Step 3/6: Refining ontology...")
    refined = await refine_ontology(
        entity_types=entity_types,
        relationship_types=relationship_types,
        domain=domain,
        key_topics=key_topics,
        llm=llm,
        max_entity_types=settings.bootstrap_max_entity_types,
        max_rel_types=settings.bootstrap_max_rel_types,
        table_schemas=table_schemas,
    )
    entity_types = refined.get("entity_types", entity_types)
    relationship_types = refined.get("relationship_types", relationship_types)

    # ── 5b. Generate domain knowledge context ──────────────────
    logger.info("Step 3b/6: Generating domain knowledge context...")
    domain_knowledge_text = await generate_domain_knowledge_context(
        sample_text=sample_text,
        domain=domain,
        subdomain=subdomain,
        language=language,
        key_topics=key_topics,
        llm=llm,
    )

    # ── 6. Generate prompts ───────────────────────────────────
    logger.info("Step 4/6: Generating domain-specific prompts...")
    prompts = await generate_prompts(
        entity_types=entity_types,
        relationship_types=relationship_types,
        domain=domain,
        language=language,
        document_types=document_types,
        key_topics=key_topics,
        llm=llm,
    )

    # ── 7. Discover boilerplate keywords ──────────────────────
    logger.info("Step 5/6: Discovering boilerplate keywords...")
    boilerplate = await discover_boilerplate_keywords(domain, document_types, llm)

    # ── 7b. Detect contradiction pairs ────────────────────────
    logger.info("Step 5b/6: Detecting contradictory relationship pairs...")
    contradiction_pairs = await detect_contradiction_pairs(relationship_types, llm)

    # ── 8. Store everything in SQLite ─────────────────────────
    logger.info("Storing ontology, prompts, and metadata in instance store...")

    version_id = store.create_ontology_version(
        name=f"bootstrap-{domain}",
        description=f"Auto-discovered from {len(pdf_paths)} documents. {subdomain}.",
        domain=domain,
        activate=True,
    )

    store.store_entity_types_batch(version_id, entity_types)
    store.store_relationship_types_batch(version_id, relationship_types)
    if contradiction_pairs:
        store.store_contradiction_pairs(version_id, contradiction_pairs)
    store.store_prompts_batch(version_id, prompts)

    # Store domain knowledge context
    if domain_knowledge_text:
        store.store_prompt(
            version_id,
            "domain_knowledge_context",
            domain_knowledge_text,
            description="Cross-document domain knowledge for extraction prompts",
        )

    # Store boilerplate keywords as a special prompt
    store.store_prompt(
        version_id,
        "boilerplate_keywords",
        json.dumps(boilerplate, ensure_ascii=False),
        description="Keywords indicating boilerplate sections to skip",
    )

    # Record bootstrap sources
    for i, pdf_path in enumerate(pdf_paths):
        page_count = len(all_pages[i]) if i < len(all_pages) else 0
        store.record_bootstrap_source(
            version_id=version_id,
            source_type="pdf",
            source_path=str(Path(pdf_path).resolve()),
            page_count=page_count,
        )

    # Store domain metadata
    store.set_meta("domain", domain)
    store.set_meta("subdomain", subdomain)
    store.set_meta("language", language)
    store.set_meta("document_types", json.dumps(document_types))
    store.set_meta("key_topics", json.dumps(key_topics))
    store.mark_bootstrapped(domain)

    # Log to activity log
    init_id = f"v{version_id}"
    init_label = f"Init: {domain}"
    items: list[tuple[str, str, str]] = []
    for etype, desc in entity_types.items():
        items.append(("entity_type", etype, desc))
    for rtype, desc in relationship_types.items():
        items.append(("relationship_type", rtype, desc))
    for key in prompts:
        items.append(("prompt", key, ""))
    if items:
        store.log_activity_batch("init", init_id, init_label, items)

    summary = {
        "domain": domain,
        "subdomain": subdomain,
        "language": language,
        "document_types": document_types,
        "key_topics": key_topics,
        "entity_types": len(entity_types),
        "relationship_types": len(relationship_types),
        "prompts_generated": len(prompts),
        "boilerplate_keywords": len(boilerplate),
        "documents_analyzed": len(pdf_paths),
        "total_pages": total_pages,
        "version_id": version_id,
    }

    logger.info(
        "Bootstrap complete: %d entity types, %d relationship types, %d prompts",
        len(entity_types),
        len(relationship_types),
        len(prompts),
    )
    return summary
