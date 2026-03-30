"""Relationship model — an edge/triple in the knowledge graph."""

from __future__ import annotations

from pydantic import BaseModel


class Relationship(BaseModel):
    """A directed relationship between two entities."""

    subject: str = ""
    subject_type: str = ""
    predicate: str = ""
    object: str = ""
    object_type: str = ""
    confidence: float = 0.5
    source_doc: str = ""
    source_section: str = ""
    verified: bool = True
    created_at: str = ""
    last_confirmed_at: str = ""
