"""Service bridge between the GUI views and SynapseOS business logic.

SynapseBridge is a thin facade that manages Settings, InstanceStore, and
GraphStore instances per-graph. Views call bridge methods instead of
touching config/storage directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from synapse.config import Settings, get_settings
from synapse.storage.instance_store import InstanceStore

logger = logging.getLogger(__name__)


class SynapseBridge:
    """Central service object shared by all GUI views."""

    def __init__(self) -> None:
        self._settings: Settings = get_settings()
        self._stores: dict[str, InstanceStore] = {}
        self._graph_cache: object | None = None  # cached GraphStore
        self._graph_cache_name: str = ""

    # -- Graph management -----------------------------------------------------

    def list_graphs(self) -> list[str]:
        """Scan ~/.synapse/dbs/ and return available graph names."""
        dbs_dir = Path.home() / ".synapse" / "dbs"
        if not dbs_dir.exists():
            return []
        return sorted(
            d.name for d in dbs_dir.iterdir()
            if d.is_dir() and (d / "instance.db").exists()
        )

    @property
    def current_graph(self) -> str:
        return self._settings.graph_name

    def switch_graph(self, graph_name: str) -> None:
        """Switch the active graph and invalidate cached stores."""
        if graph_name == self._settings.graph_name:
            return
        self._close_store(self._settings.graph_name)
        self._graph_cache = None
        self._graph_cache_name = ""
        self._settings.graph_name = graph_name
        logger.info("Switched to graph: %s", graph_name)

    @property
    def settings(self) -> Settings:
        return self._settings

    # -- InstanceStore --------------------------------------------------------

    def get_store(self, graph: str | None = None) -> InstanceStore:
        """Get or create the InstanceStore for the given (or current) graph."""
        name = graph or self._settings.graph_name
        if name not in self._stores:
            db_path = Path.home() / ".synapse" / "dbs" / name / "instance.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._stores[name] = InstanceStore(db_path)
        return self._stores[name]

    # -- GraphStore -----------------------------------------------------------

    def get_graph(self):
        """Get or create a cached GraphStore for the current graph."""
        name = self._settings.graph_name
        if self._graph_cache is None or self._graph_cache_name != name:
            from synapse.storage.graph import GraphStore
            self._graph_cache = GraphStore(
                host=self._settings.falkordb_host,
                port=self._settings.falkordb_port,
                password=self._settings.falkordb_password,
                graph_name=name,
            )
            self._graph_cache_name = name
        return self._graph_cache

    # -- Dashboard data -------------------------------------------------------

    def get_dashboard_data(self) -> dict:
        """Collect data for the dashboard view."""
        store = self.get_store()
        data: dict = {
            "graph_name": self._settings.graph_name,
            "instance_dir": str(self._settings.get_instance_dir()),
            "bootstrapped": store.is_bootstrapped(),
            "domain": store.get_meta("domain", "—"),
            "subdomain": store.get_meta("subdomain", "—"),
            "language": store.get_meta("language", "—"),
            "sessions": [],
            "node_count": 0,
            "edge_count": 0,
            "entity_type_count": 0,
            "rel_type_count": 0,
            "doc_count": 0,
            "total_pages": 0,
            "random_triples": [],
            "deep_chain": None,
            "latest_relationship": None,
        }

        try:
            data["sessions"] = store.list_sessions()
        except Exception as e:
            logger.debug("Failed to load sessions: %s", e)

        try:
            data["entity_type_count"] = len(store.get_entity_types())
            data["rel_type_count"] = len(store.get_relationship_types())
        except Exception as e:
            logger.debug("Failed to load ontology counts: %s", e)

        try:
            graph = self.get_graph()
            data["node_count"] = graph.get_node_count()
            data["edge_count"] = graph.get_edge_count()

            docs = graph.get_documents()
            data["doc_count"] = len(docs)
            data["total_pages"] = sum(d.get("page_count", 0) or 0 for d in docs)

            try:
                import random
                all_triples = graph.get_all_triples(limit=50)
                if all_triples:
                    sample = random.sample(all_triples, min(3, len(all_triples)))
                    data["random_triples"] = [
                        (str(t[0]), str(t[2]).lower().replace("_", " "), str(t[3]))
                        for t in sample
                    ]
            except Exception:
                pass

            try:
                import random
                chains = graph.query(
                    "MATCH (a)-[r1]->(b)-[r2]->(c) "
                    "WHERE NOT a:Document AND NOT a:Section "
                    "AND NOT b:Document AND NOT b:Section "
                    "AND NOT c:Document AND NOT c:Section "
                    "RETURN a.canonical_name, type(r1), b.canonical_name, type(r2), c.canonical_name "
                    "LIMIT 20"
                )
                if chains:
                    chain = random.choice(chains)
                    r1 = str(chain[1]).lower().replace("_", " ")
                    r2 = str(chain[3]).lower().replace("_", " ")
                    data["deep_chain"] = (
                        f"{chain[0]} \u2192 {r1} \u2192 {chain[2]} \u2192 {r2} \u2192 {chain[4]}"
                    )
                    data["latest_relationship"] = (
                        f"{chains[-1][0]} {str(chains[-1][1]).lower().replace('_', ' ')} {chains[-1][2]}"
                    )
            except Exception:
                pass

        except Exception as exc:
            logger.debug("Could not connect to FalkorDB: %s", exc)

        return data

    # -- Cleanup --------------------------------------------------------------

    def _close_store(self, graph_name: str) -> None:
        store = self._stores.pop(graph_name, None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass

    def close(self) -> None:
        for name in list(self._stores):
            self._close_store(name)
        self._graph_cache = None
