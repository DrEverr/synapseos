"""Deterministic table extraction from markdown sections.

Tables in markdown are parsed without LLM into structured rows stored in SQLite.
"""

from __future__ import annotations

import logging
import re

from synapse.extraction.entities import detect_tables
from synapse.models.document import Section
from synapse.models.entity import Entity
from synapse.models.relationship import Relationship
from synapse.resolution.normalizer import normalize_entity_name
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)

_TABLE_ROW_RE = re.compile(r"^\|.+\|$")


def _col_to_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return key or "col"


def parse_md_tables(text: str) -> list[dict]:
    """Parse all markdown tables in *text*.

    Returns list of ``{columns: list[str], rows: list[list[str]]}``.
    """
    text = re.sub(r"</?page_\d+>", "", text)
    tables: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _TABLE_ROW_RE.match(line):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
                rows: list[list[str]] = []
                j = i + 2
                while j < len(lines):
                    row_line = lines[j].strip()
                    if _TABLE_ROW_RE.match(row_line) and not re.match(
                        r"^\|[\s\-:|]+\|$", row_line
                    ):
                        vals = [c.strip() for c in row_line.split("|")[1:-1]]
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


def _detect_product_prefix(text: str) -> str:
    """Extract product prefix from section context (e.g., 'GDMI' from '# GDMI — MÅTT')."""
    text = re.sub(r"</?page_\d+>", "", text)
    for line in text.splitlines():
        m = re.match(r"^#{1,3}\s+(.+)", line)
        if m:
            title = m.group(1)
            prefix = title.split("—")[0].split("–")[0].split("-")[0].strip()
            if prefix:
                return prefix
    return ""


def extract_tables_deterministic(
    section: Section,
    document_title: str,
) -> tuple[list[Entity], list[Relationship], list[dict]]:
    """Parse tables deterministically. Returns (entities, relationships, sql_rows).

    *sql_rows* is a list of dicts ready for SQLite storage.
    """
    tables = parse_md_tables(section.text)
    if not tables:
        return [], [], []

    prefix = _detect_product_prefix(section.text)
    all_entities: list[Entity] = []
    all_rels: list[Relationship] = []
    all_sql_rows: list[dict] = []

    for tbl in tables:
        cols = tbl["columns"]
        if len(tbl["rows"]) < 2:
            continue

        for row_vals in tbl["rows"]:
            props: dict[str, str] = {}
            for col, val in zip(cols, row_vals):
                key = _col_to_key(col)
                if val.strip():
                    props[key] = val.strip()

            first_val = row_vals[0].strip() if row_vals else ""
            entity_text = f"{prefix} {first_val}".strip() if prefix else first_val
            if not entity_text:
                continue

            etype = "TABLE_ROW"

            entity = Entity(
                text=entity_text,
                entity_type=etype,
                confidence=1.0,
                canonical_name=normalize_entity_name(entity_text),
                properties=props,
                source_doc=document_title,
                source_section=section.node_id,
            )
            all_entities.append(entity)

            if prefix:
                all_rels.append(Relationship(
                    subject=prefix,
                    subject_type="",
                    predicate="HAS_ROW",
                    object=entity_text,
                    object_type=etype,
                    confidence=0.95,
                    source_doc=document_title,
                    source_section=section.node_id,
                ))

            sql_row = dict(props)
            sql_row["_entity_text"] = entity_text
            sql_row["_entity_type"] = etype
            all_sql_rows.append(sql_row)

    return all_entities, all_rels, all_sql_rows


async def process_section_tables(
    section: Section,
    document_title: str,
    store: InstanceStore | None = None,
) -> tuple[list[Entity], list[Relationship]]:
    """Extract entities from tables in a section and store rows in SQLite.

    Returns (entities, relationships). Also stores raw table data in InstanceStore.
    """
    tables = parse_md_tables(section.text)
    if not tables or all(len(t["rows"]) < 2 for t in tables):
        return [], []

    total_rows = sum(len(t["rows"]) for t in tables)
    logger.info(
        "Table extraction (deterministic) for section '%s': %d table(s), %d rows",
        section.title, len(tables), total_rows,
    )

    entities, rels, sql_rows = extract_tables_deterministic(section, document_title)

    if store and sql_rows:
        prefix = _detect_product_prefix(section.text)
        table_name = f"{prefix}_{section.title}".strip("_").replace(" ", "_")
        cols = list(sql_rows[0].keys()) if sql_rows else []
        try:
            store.store_table(
                source_doc=document_title,
                source_section=section.node_id,
                table_name=table_name,
                columns=cols,
                rows=sql_rows,
            )
        except Exception as e:
            logger.warning("Failed to store table in SQLite: %s", e)

    logger.info(
        "Table extraction: %d entities, %d relationships from %d rows",
        len(entities), len(rels), total_rows,
    )
    return entities, rels
