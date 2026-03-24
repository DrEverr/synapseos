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
    DOMAIN_ANALYSIS_SYSTEM,
    DOMAIN_ANALYSIS_USER,
    ONTOLOGY_DISCOVERY_INCREMENTAL_USER,
    ONTOLOGY_DISCOVERY_SYSTEM,
    ONTOLOGY_DISCOVERY_USER,
    ONTOLOGY_REFINEMENT_SYSTEM,
    ONTOLOGY_REFINEMENT_USER,
    PROMPT_GENERATION_SYSTEM,
    PROMPT_GENERATION_USER,
)
from synapse.config import Settings
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
) -> dict:
    """Step 2: Discover entity types and relationship types from document samples."""
    if existing_entity_types:
        # Incremental discovery
        user = ONTOLOGY_DISCOVERY_INCREMENTAL_USER.format(
            domain_context=domain_context,
            existing_entity_types=json.dumps(existing_entity_types, indent=2),
            existing_relationship_types=json.dumps(existing_relationship_types or {}, indent=2),
            sample_text=sample_text,
            max_entity_types=max_entity_types,
            max_rel_types=max_rel_types,
        )
    else:
        user = ONTOLOGY_DISCOVERY_USER.format(
            domain_context=domain_context,
            sample_text=sample_text,
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
) -> dict:
    """Step 3: Refine and finalize the discovered ontology."""
    result = await llm.complete_json_lenient(
        system=ONTOLOGY_REFINEMENT_SYSTEM,
        user=ONTOLOGY_REFINEMENT_USER.format(
            domain=domain,
            entity_types=json.dumps(entity_types, indent=2),
            relationship_types=json.dumps(relationship_types, indent=2),
            key_topics=", ".join(key_topics),
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
    logger.info("Step 1/5: Analyzing domain...")
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

    # ── 4. Discover ontology (may run multiple rounds for large batches) ─
    logger.info("Step 2/5: Discovering ontology...")

    # First round with initial sample
    ontology = await discover_ontology(
        sample_text=sample_text,
        domain_context=domain_context,
        llm=llm,
        max_entity_types=settings.bootstrap_max_entity_types,
        max_rel_types=settings.bootstrap_max_rel_types,
    )
    entity_types = ontology.get("entity_types", {})
    relationship_types = ontology.get("relationship_types", {})

    # Additional round(s) if we have many documents
    if len(all_pages) > 3:
        # Sample different pages for a second pass
        random.seed(42)  # Reproducible but different from first sample
        second_sample = _sample_pages(all_pages, max_pages=settings.bootstrap_sample_pages)
        ontology2 = await discover_ontology(
            sample_text=second_sample,
            domain_context=domain_context,
            llm=llm,
            max_entity_types=settings.bootstrap_max_entity_types,
            max_rel_types=settings.bootstrap_max_rel_types,
            existing_entity_types=entity_types,
            existing_relationship_types=relationship_types,
        )
        entity_types = ontology2.get("entity_types", entity_types)
        relationship_types = ontology2.get("relationship_types", relationship_types)

    # ── 5. Refine ontology ────────────────────────────────────
    logger.info("Step 3/5: Refining ontology...")
    refined = await refine_ontology(
        entity_types=entity_types,
        relationship_types=relationship_types,
        domain=domain,
        key_topics=key_topics,
        llm=llm,
        max_entity_types=settings.bootstrap_max_entity_types,
        max_rel_types=settings.bootstrap_max_rel_types,
    )
    entity_types = refined.get("entity_types", entity_types)
    relationship_types = refined.get("relationship_types", relationship_types)

    # ── 6. Generate prompts ───────────────────────────────────
    logger.info("Step 4/5: Generating domain-specific prompts...")
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
    logger.info("Step 5/5: Discovering boilerplate keywords...")
    boilerplate = await discover_boilerplate_keywords(domain, document_types, llm)

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
    store.store_prompts_batch(version_id, prompts)

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
