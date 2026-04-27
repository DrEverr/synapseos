"""Graph analytics, health, search, ontology endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from synapse.web.deps import get_graph, get_ontology

router = APIRouter(tags=["graph"])


@router.get("/graph/health")
def graph_health(graph=Depends(get_graph), ontology=Depends(get_ontology)):
    return graph.get_graph_health(ontology_types=ontology.entity_types)


@router.get("/graph/stats")
def graph_stats(graph=Depends(get_graph)):
    return {
        "nodes": graph.get_node_count(),
        "edges": graph.get_edge_count(),
        "entity_counts": graph.get_entity_counts(),
        "relationship_counts": graph.get_relationship_counts(),
    }


@router.get("/graph/search")
def search_entities(q: str = Query(..., min_length=1), limit: int = 20, graph=Depends(get_graph)):
    rows = graph.search_entities(q, limit=limit)
    return [
        {"canonical_name": r[0], "entity_type": r[1], "text": r[2]}
        for r in rows
    ]


@router.get("/graph/entity/{name}/neighbors")
def entity_neighbors(name: str, hops: int = 2, graph=Depends(get_graph)):
    rows = graph.get_neighbors(name, max_hops=hops)
    return [{"path": r[0], "rels": r[1]} for r in rows]


@router.get("/graph/entity/{name}/provenance")
def entity_provenance(name: str, graph=Depends(get_graph)):
    return graph.get_entity_provenance(name)


@router.get("/graph/triples")
def sample_triples(limit: int = 50, graph=Depends(get_graph)):
    rows = graph.get_all_triples(limit=limit)
    return [
        {"subject": r[0], "subject_type": r[1], "predicate": r[2], "object": r[3], "object_type": r[4]}
        for r in rows
    ]


@router.get("/graph/ontology")
def ontology_info(ontology=Depends(get_ontology)):
    return {
        "entity_types": ontology.entity_types,
        "relationship_types": ontology.relationship_types,
    }


@router.get("/graph/documents")
def list_documents(graph=Depends(get_graph)):
    return graph.get_documents()
