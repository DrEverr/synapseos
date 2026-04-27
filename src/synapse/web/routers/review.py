"""Entity and relationship review (verify/reject) endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from synapse.web.deps import get_graph, get_ontology, get_store

router = APIRouter(tags=["review"])


class EntityAction(BaseModel):
    canonical_name: str
    entity_type: str


class RelationshipAction(BaseModel):
    subject: str
    predicate: str
    object: str


@router.get("/review/entities")
def unverified_entities(graph=Depends(get_graph)):
    rows = graph.get_unverified_entities()
    return [
        {"canonical_name": r[0], "entity_type": r[1], "confidence": r[2], "source_docs": r[3]}
        for r in rows
    ]


@router.get("/review/relationships")
def unverified_relationships(graph=Depends(get_graph)):
    rows = graph.get_unverified_relationships()
    return [
        {"subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3], "source_doc": r[4]}
        for r in rows
    ]


@router.get("/review/triples")
def unverified_triples(graph=Depends(get_graph)):
    """Full unverified triples with subject/object types."""
    rows = graph.query(
        "MATCH (a)-[r]->(b) "
        "WHERE COALESCE(r.verified, true) = false "
        "AND NOT a:Document AND NOT a:Section "
        "AND NOT b:Document AND NOT b:Section "
        "RETURN a.canonical_name, labels(a)[0], type(r), "
        "b.canonical_name, labels(b)[0], r.source_doc "
        "ORDER BY type(r), a.canonical_name"
    )
    return [
        {
            "subject": r[0], "subject_type": r[1], "predicate": r[2],
            "object": r[3], "object_type": r[4], "source_doc": r[5],
        }
        for r in rows
    ]


@router.get("/review/entity/{name}/context")
def entity_context(name: str, graph=Depends(get_graph), store=Depends(get_store)):
    """Get enrichment context for an entity — source_text + chat Q&A."""
    result = {"source_text": "", "question": "", "answer": "", "label": ""}

    # source_text from graph node
    rows = graph.query(
        "MATCH (n) WHERE n.canonical_name = $name AND n.source_text IS NOT NULL "
        "RETURN n.source_text LIMIT 1",
        params={"name": name},
    )
    if rows and rows[0][0]:
        result["source_text"] = rows[0][0]

    # Chat Q&A from activity log
    row = store._conn.execute(
        "SELECT action_label, item_detail, created_at FROM activity_log "
        "WHERE action_type = 'chat' AND item_name = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (name,),
    ).fetchone()
    if row:
        result["label"] = row["action_label"]
        if not result["source_text"] and row["item_detail"] and len(row["item_detail"]) > 20:
            result["source_text"] = row["item_detail"]
        ep = store._conn.execute(
            "SELECT question, answer FROM reasoning_episodes "
            "WHERE (entities_added > 0 OR rels_added > 0) "
            "AND created_at <= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (row["created_at"],),
        ).fetchone()
        if ep:
            result["question"] = ep["question"]
            result["answer"] = ep["answer"]

    # Provenance
    result["provenance"] = graph.get_entity_provenance(name)

    return result


@router.get("/review/ontology")
def ontology_types(ontology=Depends(get_ontology)):
    return {
        "entity_types": ontology.entity_types,
        "relationship_types": ontology.relationship_types,
    }


@router.post("/review/entities/verify")
def verify_entity(body: EntityAction, graph=Depends(get_graph)):
    graph.verify_entity(body.canonical_name, body.entity_type)
    return {"ok": True}


@router.post("/review/entities/reject")
def reject_entity(body: EntityAction, graph=Depends(get_graph)):
    graph.reject_entity(body.canonical_name, body.entity_type)
    return {"ok": True}


@router.post("/review/relationships/verify")
def verify_relationship(body: RelationshipAction, graph=Depends(get_graph)):
    graph.verify_relationship(body.subject, body.predicate, body.object)
    return {"ok": True}


@router.post("/review/relationships/reject")
def reject_relationship(body: RelationshipAction, graph=Depends(get_graph)):
    graph.reject_relationship(body.subject, body.predicate, body.object)
    return {"ok": True}


@router.post("/review/verify-all")
def verify_all(graph=Depends(get_graph)):
    graph.verify_all_entities()
    graph.verify_all_relationships()
    return {"ok": True}
