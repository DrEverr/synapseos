"""Entity and relationship review (verify/reject) endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from synapse.web.deps import get_graph

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
