"""Orchestrates the full ingestion pipeline: parse -> extract -> resolve -> store."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from collections import defaultdict

from synapse.config import OntologyRegistry, Settings
from synapse.extraction.discovery import discover_ontology_gaps
from synapse.extraction.entities import extract_entities
from synapse.extraction.relationships import extract_relationships
from synapse.llm.client import LLMClient
from synapse.llm.templates import safe_format
from synapse.models.document import Document, Section
from synapse.models.entity import Entity
from synapse.models.relationship import Relationship
from synapse.parsers.structure import extract_document_structure
from synapse.resolution.linker import resolve_entities
from synapse.resolution.normalizer import normalize_entity_name
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

logger = logging.getLogger(__name__)


# ── Cross-section relationship inference ─────────────────────

_MAX_SAMPLES_PER_TYPE = 10

_CROSS_SECTION_USER = """These entities were extracted from DIFFERENT sections of the document "{document_title}".
Many relationships span across sections and were missed during per-section extraction.
Your task is to identify these cross-section relationships.

ENTITIES BY TYPE:
{entity_summary}

RELATIONSHIP TYPES:
{relationship_types}

Because some types have hundreds of entities, express your findings as RULES.

Return a JSON array of rule objects. Each rule describes how one entity type relates to another:

PATTERN rules (for many entities matching a naming pattern):
{{"subject_type": "TYPE_A", "predicate": "REL_NAME", "object_type": "TYPE_B",
  "rule": "prefix_match",
  "description": "Why this pattern holds"}}

{{"subject_type": "TYPE_A", "predicate": "REL_NAME", "object_type": "TYPE_B",
  "rule": "contains",
  "description": "Why this pattern holds"}}

EXPLICIT rules (for small sets — enumerate all pairs):
{{"subject_type": "TYPE_A", "predicate": "REL_NAME", "object_type": "TYPE_B",
  "rule": "explicit",
  "pairs": [{{"subject": "entity A name", "object": "entity B name"}}, ...]}}

Supported rule types:
- "prefix_match" — object name starts with subject name (case-insensitive)
- "contains" — object name contains subject name (case-insensitive)
- "explicit" — listed subject/object pairs only

RULES:
1. Only use relationship types from the list above.
2. Do NOT repeat relationships that would already exist within a single section.
3. Focus on relationships where subject and object come from DIFFERENT entity types.
4. For pattern rules, verify the pattern holds for the sample entities shown.
5. Return [] if no cross-section relationships are found."""


async def infer_cross_section_relationships(
    entities: list[Entity],
    llm: LLMClient,
    ontology: OntologyRegistry,
    document_title: str,
    store: InstanceStore | None = None,
) -> list[Relationship]:
    """Use LLM to discover relationship rules across section boundaries.

    Returns expanded relationships from the rules the LLM identifies.
    """
    if len(entities) < 2:
        return []

    # Group entities by type
    by_type: dict[str, list[Entity]] = defaultdict(list)
    for e in entities:
        by_type[e.entity_type].append(e)

    # Build entity summary (sample large types)
    summary_lines: list[str] = []
    for etype, ents in sorted(by_type.items()):
        names = [e.text for e in ents]
        if len(names) > _MAX_SAMPLES_PER_TYPE:
            shown = ", ".join(names[:_MAX_SAMPLES_PER_TYPE])
            summary_lines.append(f"{etype} ({len(names)}, showing {_MAX_SAMPLES_PER_TYPE}): {shown}")
        else:
            summary_lines.append(f"{etype} ({len(names)}): {', '.join(names)}")
    entity_summary = "\n".join(summary_lines)

    # Get system prompt
    system_prompt = None
    if store:
        system_prompt = store.get_prompt("relationship_extraction_system")
    if not system_prompt:
        system_prompt = "You are an expert relationship extraction system for technical documents."

    user_prompt = _CROSS_SECTION_USER.format(
        document_title=document_title,
        entity_summary=entity_summary,
        relationship_types=ontology.format_relationship_types(),
    )

    try:
        result = await llm.complete_json_lenient(
            system=system_prompt, user=user_prompt, max_tokens=4096
        )
    except Exception as e:
        logger.error("Cross-section relationship inference failed: %s", e)
        return []

    if isinstance(result, dict):
        for key in ("rules", "relationships", "data", "result"):
            if key in result and isinstance(result[key], list):
                result = result[key]
                break
        else:
            result = []

    if not isinstance(result, list):
        return []

    # Expand rules into concrete relationships
    return _expand_rules(result, by_type, document_title)


def _expand_rules(
    rules: list[dict],
    by_type: dict[str, list[Entity]],
    source_doc: str,
) -> list[Relationship]:
    """Expand LLM-generated rules into concrete Relationship objects."""
    rels: list[Relationship] = []

    # Build lookup: normalized name → Entity (per type)
    lookup: dict[str, dict[str, Entity]] = {}
    for etype, ents in by_type.items():
        lookup[etype] = {}
        for e in ents:
            lookup[etype][normalize_entity_name(e.text)] = e
            lookup[etype][e.text.lower()] = e

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        subj_type = rule.get("subject_type", "")
        obj_type = rule.get("object_type", "")
        predicate = rule.get("predicate", "").upper()
        rule_kind = rule.get("rule", "")

        subjects = by_type.get(subj_type, [])
        objects = by_type.get(obj_type, [])

        if not subjects or not objects or not predicate:
            continue

        if rule_kind == "prefix_match":
            # Sort subjects longest-first so "GDC Flex" matches before "GDC"
            sorted_subjects = sorted(subjects, key=lambda e: len(e.text), reverse=True)
            for obj in objects:
                obj_lower = obj.text.lower()
                for subj in sorted_subjects:
                    if obj_lower.startswith(subj.text.lower()):
                        rels.append(_make_rel(subj, predicate, obj, source_doc))
                        break

        elif rule_kind == "contains":
            sorted_subjects = sorted(subjects, key=lambda e: len(e.text), reverse=True)
            for obj in objects:
                obj_lower = obj.text.lower()
                for subj in sorted_subjects:
                    if subj.text.lower() in obj_lower:
                        rels.append(_make_rel(subj, predicate, obj, source_doc))
                        break

        elif rule_kind == "explicit":
            pairs = rule.get("pairs", [])
            subj_lookup = lookup.get(subj_type, {})
            obj_lookup = lookup.get(obj_type, {})
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                s_name = pair.get("subject", "")
                o_name = pair.get("object", "")
                s_ent = subj_lookup.get(s_name.lower()) or subj_lookup.get(
                    normalize_entity_name(s_name)
                )
                o_ent = obj_lookup.get(o_name.lower()) or obj_lookup.get(
                    normalize_entity_name(o_name)
                )
                if s_ent and o_ent:
                    rels.append(_make_rel(s_ent, predicate, o_ent, source_doc))
                else:
                    logger.debug(
                        "Cross-section explicit pair not matched: %s → %s", s_name, o_name
                    )

    logger.info("Expanded %d cross-section relationships from %d rules", len(rels), len(rules))
    return rels


def _make_rel(subj: Entity, predicate: str, obj: Entity, source_doc: str) -> Relationship:
    return Relationship(
        subject=subj.text,
        subject_type=subj.entity_type,
        predicate=predicate,
        object=obj.text,
        object_type=obj.entity_type,
        confidence=0.90,
        source_doc=source_doc,
        source_section="cross_section_inference",
    )


async def process_section(
    section: Section,
    llm: LLMClient,
    ontology: OntologyRegistry,
    document_title: str,
    store: InstanceStore | None = None,
) -> tuple[list[Entity], list[Relationship]]:
    """Extract entities and relationships from a single section.

    For sections containing large tables (>20 rows), splits the table into
    batches and runs extraction on each batch.  After extraction, verifies
    completeness against expected row count and retries once if <70%.
    """
    from synapse.extraction.entities import detect_tables, split_table_section

    tables = detect_tables(section.text)

    if tables and any(t["row_count"] > 20 for t in tables):
        sub_sections = split_table_section(section)
        all_entities: list[Entity] = []
        for sub in sub_sections:
            ents = await extract_entities(sub, llm, ontology, document_title, store)
            all_entities.extend(ents)
        # Run relationship extraction ONCE on the full section with ALL entities
        all_relationships = await extract_relationships(
            section, all_entities, llm, ontology, document_title, store
        )
    else:
        all_entities = await extract_entities(section, llm, ontology, document_title, store)
        all_relationships = await extract_relationships(
            section, all_entities, llm, ontology, document_title, store
        )

    # Completeness verification for table sections (max 1 retry)
    if tables:
        expected_rows = sum(t["row_count"] for t in tables)
        if expected_rows > 5 and len(all_entities) < expected_rows * 0.7:
            logger.warning(
                "Table completeness: %d entities for %d expected rows in '%s', retrying",
                len(all_entities),
                expected_rows,
                section.title,
            )
            existing = {e.text.lower() for e in all_entities}
            retry_ents = await extract_entities(section, llm, ontology, document_title, store)
            for e in retry_ents:
                if e.text.lower() not in existing:
                    all_entities.append(e)
                    existing.add(e.text.lower())

    return all_entities, all_relationships


async def process_document(
    pdf_path: str,
    llm: LLMClient,
    ontology: OntologyRegistry,
    settings: Settings,
    graph: GraphStore | None = None,
    dry_run: bool = False,
    text_cache: TextCache | None = None,
    store: InstanceStore | None = None,
) -> tuple[Document, list[Entity], list[Relationship]]:
    """Process a single PDF through the full pipeline."""
    path = Path(pdf_path)
    logger.info("=" * 60)
    logger.info("Processing document: %s", path.name)

    # Step 1: Extract document structure (with TOC detection)
    doc = await extract_document_structure(
        pdf_path=str(path),
        llm=llm,
        toc_scan_pages=settings.toc_scan_pages,
        toc_accuracy_threshold=settings.toc_accuracy_threshold,
        max_pages_per_node=settings.structure_max_pages_per_node,
        max_tokens_per_node=settings.structure_max_tokens_per_node,
    )

    # Step 1b: Ontology discovery — check if document introduces types not in ontology
    new_etypes, new_rtypes = await discover_ontology_gaps(
        doc=doc,
        llm=llm,
        ontology=ontology,
        store=store,
    )
    if new_etypes or new_rtypes:
        parts: list[str] = []
        if new_etypes:
            parts.append("Entity types: " + ", ".join(f"{k} ({v})" for k, v in new_etypes.items()))
        if new_rtypes:
            parts.append(
                "Relationship types: " + ", ".join(f"{k} ({v})" for k, v in new_rtypes.items())
            )
        logger.warning(
            "Ontology auto-expanded with %d entity type(s), %d relationship type(s):\n  %s",
            len(new_etypes),
            len(new_rtypes),
            "\n  ".join(parts),
        )
        # Update graph indexes for newly added entity types
        if new_etypes and graph:
            graph.ensure_indexes(ontology.entity_types)

    # Step 2: Extract entities and relationships from each leaf section
    all_entities: list[Entity] = []
    all_relationships: list[Relationship] = []
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def _process_with_semaphore(section: Section) -> tuple[list[Entity], list[Relationship]]:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    process_section(section, llm, ontology, doc.title, store),
                    timeout=settings.section_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Section '%s' timed out after %.0fs", section.title, settings.section_timeout
                )
                return [], []
            except Exception as e:
                logger.error("Section '%s' failed: %s", section.title, e)
                return [], []

    leaves = doc.leaf_sections()
    logger.info(
        "Processing %d leaf sections (concurrency=%d)", len(leaves), settings.max_concurrency
    )

    tasks = [_process_with_semaphore(section) for section in leaves]
    results = await asyncio.gather(*tasks)

    for entities, relationships in results:
        all_entities.extend(entities)
        all_relationships.extend(relationships)

    logger.info(
        "Raw extraction: %d entities, %d relationships", len(all_entities), len(all_relationships)
    )

    # Step 3: Entity resolution (dedup)
    all_entities = resolve_entities(all_entities, settings.fuzzy_match_threshold)

    # Step 3b: Cross-section relationship inference (LLM-driven rules)
    inferred = await infer_cross_section_relationships(
        all_entities, llm, ontology, doc.title, store
    )
    if inferred:
        all_relationships.extend(inferred)
        logger.info("Added %d cross-section relationships", len(inferred))

    # Step 4: Cache section text
    if text_cache:
        cache_items = {}
        for section in doc.leaf_sections():
            section_id = f"{doc.id}:{section.node_id}"
            if section.text:
                cache_items[section_id] = section.text
        text_cache.store_batch(cache_items)

    # Step 5: Store to graph
    if not dry_run and graph:
        graph.store_document(doc)
        for entity in all_entities:
            graph.store_entity(entity)
            if entity.source_section:
                graph.link_entity_to_section(entity, doc.id)
        for rel in all_relationships:
            graph.store_relationship(rel)
        logger.info(
            "Stored to graph: %d entities, %d relationships",
            len(all_entities),
            len(all_relationships),
        )

    return doc, all_entities, all_relationships


async def ingest_files(
    pdf_files: list[str],
    settings: Settings,
    reset: bool = False,
    dry_run: bool = False,
) -> dict:
    """Ingest multiple PDF files through the full pipeline."""
    store = settings.get_instance_store()
    ontology = OntologyRegistry(store=store, ontology_name=settings.ontology)

    model = settings.extraction_model or settings.llm_model
    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model,
        timeout=settings.llm_timeout,
    )
    logger.info("Extraction using model: %s", model)
    text_cache = TextCache(cache_dir=settings.get_text_cache_dir())

    graph: GraphStore | None = None
    if not dry_run:
        graph = GraphStore(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
            graph_name=settings.graph_name,
        )
        if reset:
            graph.reset()
        graph.ensure_indexes(ontology.entity_types)

    total_entities = 0
    total_relationships = 0
    errors: list[dict] = []

    import uuid as _uuid
    ingest_id = str(_uuid.uuid4())[:8]

    for pdf_path in pdf_files:
        try:
            doc, entities, rels = await process_document(
                pdf_path=pdf_path,
                llm=llm,
                ontology=ontology,
                settings=settings,
                graph=graph,
                dry_run=dry_run,
                text_cache=text_cache,
                store=store,
            )
            total_entities += len(entities)
            total_relationships += len(rels)

            # Log to activity log
            if not dry_run:
                items: list[tuple[str, str, str]] = []
                for e in entities:
                    items.append(("entity", e.canonical_name, e.entity_type))
                for r in rels:
                    items.append(("relationship", f"{r.subject} → {r.predicate} → {r.object}", ""))
                if items:
                    label = f"Ingest: {Path(pdf_path).name}"
                    store.log_activity_batch("ingest", ingest_id, label, items)
        except Exception as e:
            logger.error("Failed to process %s: %s", pdf_path, e)
            errors.append({"file": pdf_path, "error": str(e)})

    summary: dict = {
        "documents": len(pdf_files),
        "total_entities": total_entities,
        "total_relationships": total_relationships,
        "errors": errors,
    }

    if graph:
        summary["graph_nodes"] = graph.get_node_count()
        summary["graph_edges"] = graph.get_edge_count()
        summary["entity_counts"] = graph.get_entity_counts()
        summary["relationship_counts"] = graph.get_relationship_counts()

    store.close()
    return summary
