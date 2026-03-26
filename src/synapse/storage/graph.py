"""FalkorDB graph store — CRUD operations with canonical-name MERGE strategy."""

from __future__ import annotations

import json
import logging
from typing import Any

from falkordb import FalkorDB

from synapse.models.document import Document
from synapse.models.entity import Entity
from synapse.models.relationship import Relationship

logger = logging.getLogger(__name__)


class GraphStore:
    """FalkorDB wrapper with MERGE-on-canonical-name strategy."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: str = "",
        graph_name: str = "synapse_kg",
    ) -> None:
        kwargs: dict[str, Any] = {"host": host, "port": port}
        if password:
            kwargs["password"] = password
        self._db = FalkorDB(**kwargs)
        self._graph = self._db.select_graph(graph_name)

    def ensure_indexes(self, entity_types: dict[str, str]) -> None:
        """Create indexes on canonical_name for each entity type, plus Document and Section."""
        for etype in entity_types:
            try:
                self._graph.query(f"CREATE INDEX FOR (n:{etype}) ON (n.canonical_name)")
            except Exception:
                pass
        for label in ["Document", "Section"]:
            try:
                self._graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
            except Exception:
                pass

    def store_document(self, doc: Document) -> None:
        """Store a Document and its Section tree in the graph."""
        # Serialize section tree for storage
        tree_json = json.dumps([self._section_to_dict(s) for s in doc.sections], ensure_ascii=False)

        self._graph.query(
            """MERGE (d:Document {id: $id})
               ON CREATE SET d.filename = $filename, d.title = $title,
                            d.name = $title, d.text = $title,
                            d.canonical_name = toLower($title),
                            d.page_count = $page_count, d.tree_structure_json = $tree_json,
                            d.ingested_at = $ingested_at
               ON MATCH SET d.tree_structure_json = $tree_json""",
            params={
                "id": doc.id,
                "filename": doc.filename,
                "title": doc.title,
                "page_count": doc.page_count,
                "tree_json": tree_json,
                "ingested_at": doc.ingested_at,
            },
        )

        # Store all sections
        for section in doc.all_sections():
            section_id = f"{doc.id}:{section.node_id}"
            self._graph.query(
                """MERGE (s:Section {id: $id})
                   ON CREATE SET s.document_id = $doc_id, s.node_id = $node_id,
                                s.title = $title, s.name = $title,
                                s.text = $title,
                                s.canonical_name = toLower($title),
                                s.start_page = $start,
                                s.end_page = $end, s.summary = $summary""",
                params={
                    "id": section_id,
                    "doc_id": doc.id,
                    "node_id": section.node_id,
                    "title": section.title,
                    "start": section.start_page,
                    "end": section.end_page,
                    "summary": section.summary or "",
                },
            )
            # BELONGS_TO edge
            self._graph.query(
                """MATCH (s:Section {id: $sid}), (d:Document {id: $did})
                   MERGE (s)-[:BELONGS_TO]->(d)""",
                params={"sid": section_id, "did": doc.id},
            )

    def store_entity(self, entity: Entity) -> None:
        """MERGE an entity node by (canonical_name, label). Updates confidence on match."""
        label = entity.entity_type
        props_json = (
            json.dumps(entity.properties, ensure_ascii=False) if entity.properties else "{}"
        )

        self._graph.query(
            f"""MERGE (n:{label} {{canonical_name: $canonical_name}})
                ON CREATE SET n.id = $id, n.text = $text, n.name = $text,
                             n.confidence = $confidence, n.properties = $props,
                             n.source_docs = $source_doc,
                             n.verified = $verified
                ON MATCH SET n.confidence = CASE WHEN $confidence > n.confidence
                             THEN $confidence ELSE n.confidence END,
                             n.source_docs = n.source_docs + ', ' + $source_doc,
                             n.name = $text,
                             n.verified = CASE WHEN n.verified = true THEN true ELSE $verified END""",
            params={
                "canonical_name": entity.canonical_name,
                "id": entity.id,
                "text": entity.text,
                "confidence": entity.confidence,
                "props": props_json,
                "source_doc": entity.source_doc,
                "verified": entity.verified,
            },
        )

    def link_entity_to_section(self, entity: Entity, doc_id: str) -> None:
        """Create EXTRACTED_FROM edge from entity to its source section."""
        section_id = f"{doc_id}:{entity.source_section}"
        label = entity.entity_type
        self._graph.query(
            f"""MATCH (n:{label} {{canonical_name: $name}}), (s:Section {{id: $sid}})
                MERGE (n)-[:EXTRACTED_FROM]->(s)""",
            params={"name": entity.canonical_name, "sid": section_id},
        )

    def store_relationship(self, rel: Relationship) -> None:
        """Store a relationship by MATCHing entities on canonical_name + label."""
        subj_label = rel.subject_type
        obj_label = rel.object_type
        pred = rel.predicate

        if not subj_label or not obj_label or not pred:
            logger.warning("Skipping relationship with missing type/predicate: %s", rel)
            return

        from synapse.resolution.normalizer import normalize_entity_name

        subj_name = normalize_entity_name(rel.subject)
        obj_name = normalize_entity_name(rel.object)

        try:
            self._graph.query(
                f"""MATCH (a:{subj_label} {{canonical_name: $subj}}),
                          (b:{obj_label} {{canonical_name: $obj}})
                    MERGE (a)-[r:{pred}]->(b)
                    ON CREATE SET r.confidence = $confidence, r.source_doc = $source_doc,
                                  r.verified = $verified""",
                params={
                    "subj": subj_name,
                    "obj": obj_name,
                    "confidence": rel.confidence,
                    "source_doc": rel.source_doc,
                    "verified": rel.verified,
                },
            )
        except Exception as e:
            logger.debug(
                "Failed to store relationship %s -[%s]-> %s: %s", rel.subject, pred, rel.object, e
            )

    def reset(self) -> None:
        """Delete all nodes and edges in the graph."""
        try:
            self._graph.query("MATCH (n) DETACH DELETE n")
            logger.info("Graph reset complete")
        except Exception as e:
            logger.warning("Graph reset failed: %s", e)

    def query(self, cypher: str, params: dict | None = None) -> list:
        """Execute a Cypher query and return the result rows."""
        result = self._graph.query(cypher, params=params or {})
        return [list(row) for row in result.result_set]

    # --- Analytics ---

    def get_node_count(self) -> int:
        result = self.query("MATCH (n) RETURN count(n)")
        return result[0][0] if result else 0

    def get_edge_count(self) -> int:
        result = self.query("MATCH ()-[r]->() RETURN count(r)")
        return result[0][0] if result else 0

    def get_entity_counts(self) -> dict[str, int]:
        result = self.query(
            "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
            "RETURN labels(n)[0], count(n) ORDER BY count(n) DESC"
        )
        return {row[0]: row[1] for row in result if row[0]}

    def get_relationship_counts(self) -> dict[str, int]:
        result = self.query(
            "MATCH ()-[r]->() WHERE NOT type(r) IN ['BELONGS_TO', 'EXTRACTED_FROM'] "
            "RETURN type(r), count(r) ORDER BY count(r) DESC"
        )
        return {row[0]: row[1] for row in result if row[0]}

    def get_all_triples(self, limit: int = 100) -> list:
        result = self.query(
            "MATCH (a)-[r]->(b) "
            "WHERE NOT a:Document AND NOT a:Section AND NOT b:Document AND NOT b:Section "
            f"RETURN a.canonical_name, labels(a)[0], type(r), b.canonical_name, labels(b)[0] LIMIT {limit}"
        )
        return result

    def find_duplicates(self) -> list:
        result = self.query(
            "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
            "WITH n.canonical_name AS name, labels(n)[0] AS label, count(*) AS cnt "
            "WHERE cnt > 1 RETURN name, label, cnt ORDER BY cnt DESC"
        )
        return result

    def get_documents(self) -> list[dict]:
        result = self.query(
            "MATCH (d:Document) RETURN d.id, d.filename, d.title, d.page_count, d.tree_structure_json"
        )
        return [
            {
                "id": row[0],
                "filename": row[1],
                "title": row[2],
                "page_count": row[3],
                "tree_json": row[4],
            }
            for row in result
        ]

    def search_entities(self, query: str, limit: int = 20) -> list:
        result = self.query(
            "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
            f"AND toLower(n.canonical_name) CONTAINS toLower('{query}') "
            f"RETURN n.canonical_name, labels(n)[0], n.text LIMIT {limit}"
        )
        return result

    def get_neighbors(self, canonical_name: str, max_hops: int = 2) -> list:
        result = self.query(
            f"MATCH p = (a)-[*1..{max_hops}]-(b) "
            f"WHERE toLower(a.canonical_name) CONTAINS toLower('{canonical_name}') "
            "RETURN [n in nodes(p) | n.canonical_name] AS path, "
            "[r in relationships(p) | type(r)] AS rels LIMIT 30"
        )
        return result

    def _section_to_dict(self, section: Any) -> dict:
        """Recursively convert a Section to a serializable dict."""
        return {
            "title": section.title,
            "start_page": section.start_page,
            "end_page": section.end_page,
            "node_id": section.node_id,
            "summary": section.summary or "",
            "children": [self._section_to_dict(c) for c in section.children],
        }

    # ── Verification / Review ──────────────────────────────────

    def get_unverified_entities(self) -> list:
        """Return entities where verified=false."""
        return self.query(
            "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
            "AND COALESCE(n.verified, true) = false "
            "RETURN n.canonical_name, labels(n)[0], n.confidence, n.source_docs "
            "ORDER BY labels(n)[0], n.canonical_name"
        )

    def get_unverified_relationships(self) -> list:
        """Return relationships where verified=false."""
        return self.query(
            "MATCH (a)-[r]->(b) WHERE COALESCE(r.verified, true) = false "
            "AND NOT a:Document AND NOT a:Section "
            "AND NOT b:Document AND NOT b:Section "
            "RETURN a.canonical_name, type(r), b.canonical_name, r.confidence, r.source_doc "
            "ORDER BY type(r), a.canonical_name"
        )

    def verify_entity(self, canonical_name: str, entity_type: str) -> None:
        """Mark an entity as verified."""
        self._graph.query(
            f"MATCH (n:{entity_type} {{canonical_name: $name}}) SET n.verified = true",
            params={"name": canonical_name},
        )

    def reject_entity(self, canonical_name: str, entity_type: str) -> None:
        """Delete an unverified entity and all its relationships."""
        self._graph.query(
            f"MATCH (n:{entity_type} {{canonical_name: $name}}) "
            "WHERE COALESCE(n.verified, true) = false DETACH DELETE n",
            params={"name": canonical_name},
        )

    def verify_relationship(self, subj: str, predicate: str, obj: str) -> None:
        """Mark a relationship as verified."""
        self._graph.query(
            f"MATCH (a {{canonical_name: $subj}})-[r:{predicate}]->(b {{canonical_name: $obj}}) "
            "SET r.verified = true",
            params={"subj": subj, "obj": obj},
        )

    def reject_relationship(self, subj: str, predicate: str, obj: str) -> None:
        """Delete an unverified relationship."""
        self._graph.query(
            f"MATCH (a {{canonical_name: $subj}})-[r:{predicate}]->(b {{canonical_name: $obj}}) "
            "WHERE COALESCE(r.verified, true) = false DELETE r",
            params={"subj": subj, "obj": obj},
        )

    def migrate_verified_flag(self) -> None:
        """Set verified=true on all existing nodes/relationships that lack the flag."""
        self._graph.query(
            "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
            "AND NOT exists(n.verified) SET n.verified = true"
        )
        self._graph.query(
            "MATCH ()-[r]->() WHERE NOT exists(r.verified) SET r.verified = true"
        )
