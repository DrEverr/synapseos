"""Orchestrates the full ingestion pipeline: parse -> extract -> resolve -> store."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from synapse.config import OntologyRegistry, Settings
from synapse.extraction.entities import extract_entities
from synapse.extraction.relationships import extract_relationships
from synapse.llm.client import LLMClient
from synapse.models.document import Document, Section
from synapse.models.entity import Entity
from synapse.models.relationship import Relationship
from synapse.parsers.structure import extract_document_structure
from synapse.resolution.linker import resolve_entities
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

logger = logging.getLogger(__name__)


async def process_section(
    section: Section,
    llm: LLMClient,
    ontology: OntologyRegistry,
    document_title: str,
    store: InstanceStore | None = None,
) -> tuple[list[Entity], list[Relationship]]:
    """Extract entities and relationships from a single section."""
    entities = await extract_entities(section, llm, ontology, document_title, store)
    relationships = await extract_relationships(
        section, entities, llm, ontology, document_title, store
    )
    return entities, relationships


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

    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout,
    )
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
