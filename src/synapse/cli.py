"""CLI entry points for SynapseOS3: init, ingest, chat, inspect, status."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click

from synapse.config import OntologyRegistry, Settings, get_settings


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("--log-level", default=None, help="Logging level")
@click.pass_context
def main(ctx: click.Context, log_level: str | None) -> None:
    """SynapseOS3 — Domain-Agnostic Knowledge Operating System.

    \b
    Lifecycle:
      1. synapse init <docs>    Bootstrap ontology & prompts from documents
      2. synapse ingest <docs>  Extract KG using generated ontology
      3. synapse chat            Chat with the knowledge graph
      4. synapse ingest <more>  Grow the KG with new documents

    \b
    Management:
      synapse status            Show instance state
      synapse inspect            Inspect the knowledge graph
      synapse versions          List ontology versions
    """
    ctx.ensure_object(dict)
    settings = get_settings()
    if log_level:
        settings.log_level = log_level
    setup_logging(settings.log_level)
    ctx.obj["settings"] = settings


# ══════════════════════════════════════════════════════════════
# init — Bootstrap ontology + prompts from a batch of documents
# ══════════════════════════════════════════════════════════════


@main.command()
@click.argument("paths", nargs=-1, required=True)
@click.pass_context
def init(ctx: click.Context, paths: tuple[str, ...]) -> None:
    """Bootstrap ontology and prompts from a batch of documents.

    \b
    This is the first step for a new instance. It:
    1. Analyzes the documents to identify the domain
    2. Discovers entity types and relationship types
    3. Generates domain-specific extraction & reasoning prompts
    4. Stores everything in the instance SQLite database

    \b
    Examples:
      synapse init ./docs/                    # all PDFs in directory
      synapse init report.pdf data.pdf        # specific files
    """
    settings: Settings = ctx.obj["settings"]

    if not settings.llm_api_key:
        click.echo("Error: SYNAPSE_LLM_API_KEY is not set.")
        sys.exit(1)

    pdf_files = _resolve_pdf_paths(paths)
    if not pdf_files:
        click.echo("Error: no PDF files found.")
        sys.exit(1)

    store = settings.get_instance_store()

    if store.is_bootstrapped():
        click.echo("Warning: This instance has already been bootstrapped.")
        click.echo(f"  Domain: {store.get_meta('domain')}")
        click.echo(f"  Bootstrapped: {store.get_meta('bootstrap_timestamp')}")
        if not click.confirm("Re-bootstrap? (creates a new ontology version)"):
            return

    click.echo(f"Bootstrapping from {len(pdf_files)} PDF(s):")
    for f in pdf_files:
        click.echo(f"  {f}")
    click.echo(f"Model: {settings.llm_model}")
    click.echo(f"Instance: {settings.get_instance_dir()}")

    from synapse.bootstrap.pipeline import bootstrap

    summary = asyncio.run(bootstrap(pdf_files, settings, store))

    click.echo("\n" + "=" * 60)
    click.echo("BOOTSTRAP COMPLETE")
    click.echo("=" * 60)
    click.echo(f"Domain: {summary['domain']} / {summary['subdomain']}")
    click.echo(f"Language: {summary['language']}")
    click.echo(f"Entity types discovered: {summary['entity_types']}")
    click.echo(f"Relationship types discovered: {summary['relationship_types']}")
    click.echo(f"Prompts generated: {summary['prompts_generated']}")
    click.echo(
        f"Documents analyzed: {summary['documents_analyzed']} ({summary['total_pages']} pages)"
    )
    click.echo(f"\nOntology version: {summary['version_id']}")
    click.echo(f"Instance DB: {settings.get_instance_dir() / 'instance.db'}")
    click.echo("\nNext step: synapse ingest <docs>")

    store.close()


# ══════════════════════════════════════════════════════════════
# ingest — Extract KG from documents using generated ontology
# ══════════════════════════════════════════════════════════════


@main.command()
@click.argument("paths", nargs=-1, required=True)
@click.option("--reset", is_flag=True, help="Reset graph before ingestion")
@click.option("--dry-run", is_flag=True, help="Extract but don't store")
@click.pass_context
def ingest(ctx: click.Context, paths: tuple[str, ...], reset: bool, dry_run: bool) -> None:
    """Ingest PDF documents into the knowledge graph.

    \b
    Requires: synapse init must have been run first.

    \b
    Examples:
      synapse ingest ./docs/
      synapse ingest report.pdf datasheet.pdf
      synapse ingest ./docs/ --reset
    """
    settings: Settings = ctx.obj["settings"]

    if not settings.llm_api_key:
        click.echo("Error: SYNAPSE_LLM_API_KEY is not set.")
        sys.exit(1)

    store = settings.get_instance_store()
    if not store.is_bootstrapped():
        click.echo("Error: Instance not bootstrapped. Run 'synapse init <docs>' first.")
        store.close()
        sys.exit(1)

    pdf_files = _resolve_pdf_paths(paths)
    if not pdf_files:
        click.echo("Error: no PDF files found.")
        sys.exit(1)

    click.echo(f"Ingesting {len(pdf_files)} PDF(s):")
    for f in pdf_files:
        click.echo(f"  {f}")
    click.echo(f"Domain: {store.get_meta('domain')}")
    click.echo(f"Model: {settings.llm_model}")
    if reset:
        click.echo("Graph will be RESET before ingestion.")
    if dry_run:
        click.echo("DRY RUN mode.")

    from synapse.extraction.pipeline import ingest_files

    summary = asyncio.run(ingest_files(pdf_files, settings, reset=reset, dry_run=dry_run))

    click.echo("\n" + "=" * 60)
    click.echo("INGESTION COMPLETE")
    click.echo("=" * 60)
    click.echo(f"Documents processed: {summary['documents']}")
    click.echo(f"Entities extracted: {summary['total_entities']}")
    click.echo(f"Relationships extracted: {summary['total_relationships']}")

    if summary.get("graph_nodes") is not None:
        click.echo(f"\nGraph: {summary['graph_nodes']} nodes, {summary['graph_edges']} edges")
        if summary.get("entity_counts"):
            click.echo("\nEntities by type:")
            for etype, count in summary["entity_counts"].items():
                click.echo(f"  {etype}: {count}")

    if summary.get("errors"):
        click.echo(f"\nErrors ({len(summary['errors'])}):")
        for err in summary["errors"]:
            click.echo(f"  {err['file']}: {err['error']}")

    store.close()


# ══════════════════════════════════════════════════════════════
# chat — Interactive Q&A with the knowledge graph
# ══════════════════════════════════════════════════════════════


@main.command()
@click.option("--query", "-q", default=None, help="Single query (non-interactive)")
@click.option("--verbose", "-v", is_flag=True, help="Show reasoning trace")
@click.pass_context
def chat(ctx: click.Context, query: str | None, verbose: bool) -> None:
    """Chat with the knowledge graph using multi-hop reasoning."""
    settings: Settings = ctx.obj["settings"]

    if not settings.llm_api_key:
        click.echo("Error: SYNAPSE_LLM_API_KEY is not set.")
        sys.exit(1)

    store = settings.get_instance_store()
    ontology = OntologyRegistry(store=store, ontology_name=settings.ontology)

    from synapse.chat.reasoning import reason
    from synapse.llm.client import LLMClient
    from synapse.storage.graph import GraphStore
    from synapse.storage.text_cache import TextCache

    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout,
    )
    graph = GraphStore(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        password=settings.falkordb_password,
        graph_name=settings.graph_name,
    )
    text_cache = TextCache(cache_dir=settings.get_text_cache_dir())

    node_count = graph.get_node_count()
    if node_count == 0:
        click.echo("Warning: Graph is empty. Run 'synapse ingest' first.")

    domain = store.get_meta("domain", "unknown")

    if query:
        answer = asyncio.run(
            reason(
                question=query,
                graph=graph,
                llm=llm,
                ontology=ontology,
                max_steps=settings.max_reasoning_steps,
                doom_threshold=settings.doom_loop_threshold,
                verbose=verbose,
                text_cache=text_cache,
                reasoning_timeout=settings.reasoning_timeout,
                step_max_tokens=settings.reasoning_step_max_tokens,
                store=store,
            )
        )
        click.echo(f"\nAnswer: {answer}")
    else:
        click.echo(f"SynapseOS3 Chat — {domain} ({node_count} nodes)")
        click.echo("Type 'quit' to exit.")
        click.echo("-" * 40)
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                click.echo("\nBye!")
                break
            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                click.echo("Bye!")
                break
            answer = asyncio.run(
                reason(
                    question=user_input,
                    graph=graph,
                    llm=llm,
                    ontology=ontology,
                    max_steps=settings.max_reasoning_steps,
                    doom_threshold=settings.doom_loop_threshold,
                    verbose=verbose,
                    text_cache=text_cache,
                    reasoning_timeout=settings.reasoning_timeout,
                    step_max_tokens=settings.reasoning_step_max_tokens,
                    store=store,
                )
            )
            click.echo(f"\nSynapseOS: {answer}")

    store.close()


# ══════════════════════════════════════════════════════════════
# inspect — Inspect the knowledge graph
# ══════════════════════════════════════════════════════════════


@main.command()
@click.option("--duplicates", is_flag=True, help="Show duplicates")
@click.option("--triples", is_flag=True, help="Show all triples")
@click.option("--tree", is_flag=True, help="Show document trees")
@click.option("--cypher", default=None, help="Execute Cypher query")
@click.option("--limit", default=100, help="Limit results")
@click.pass_context
def inspect(
    ctx: click.Context, duplicates: bool, triples: bool, tree: bool, cypher: str | None, limit: int
) -> None:
    """Inspect the knowledge graph."""
    settings: Settings = ctx.obj["settings"]
    from synapse.storage.graph import GraphStore

    graph = GraphStore(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        password=settings.falkordb_password,
        graph_name=settings.graph_name,
    )

    if cypher:
        try:
            result = graph.query(cypher)
            if result:
                for row in result[:limit]:
                    click.echo(" | ".join(str(cell) for cell in row))
                click.echo(f"\n({len(result)} rows)")
            else:
                click.echo("(no results)")
        except Exception as e:
            click.echo(f"Query error: {e}")
        return

    if duplicates:
        dupes = graph.find_duplicates()
        if dupes:
            for name, label, count in dupes:
                click.echo(f"  {name} ({label}): {count} instances")
        else:
            click.echo("No duplicates found.")
        return

    if triples:
        triple_list = graph.get_all_triples(limit=limit)
        if triple_list:
            click.echo(
                f"{'Subject':<30} {'Type':<15} {'Predicate':<20} {'Object':<30} {'Type':<15}"
            )
            click.echo("-" * 110)
            for subj, stype, pred, obj, otype in triple_list:
                click.echo(
                    f"{str(subj):<30} {str(stype):<15} {str(pred):<20} {str(obj):<30} {str(otype):<15}"
                )
            click.echo(f"\n({len(triple_list)} triples)")
        else:
            click.echo("No triples found.")
        return

    if tree:
        docs = graph.get_documents()
        if docs:
            for doc in docs:
                click.echo(f"\nDocument: {doc['title']} ({doc['filename']})")
                tree_json = doc.get("tree_json", "[]")
                try:
                    sections = json.loads(tree_json) if tree_json else []
                    _print_tree(sections, indent=2)
                except json.JSONDecodeError:
                    click.echo("  (invalid tree)")
        else:
            click.echo("No documents found.")
        return

    # Default overview
    click.echo("SynapseOS3 Knowledge Graph")
    click.echo("=" * 40)
    click.echo(f"Nodes: {graph.get_node_count()}")
    click.echo(f"Edges: {graph.get_edge_count()}")

    entity_counts = graph.get_entity_counts()
    if entity_counts:
        click.echo("\nEntities:")
        for etype, count in entity_counts.items():
            click.echo(f"  {etype}: {count}")

    rel_counts = graph.get_relationship_counts()
    if rel_counts:
        click.echo("\nRelationships:")
        for rtype, count in rel_counts.items():
            click.echo(f"  {rtype}: {count}")

    docs = graph.get_documents()
    if docs:
        click.echo(f"\nDocuments: {len(docs)}")
        for doc in docs:
            click.echo(f"  {doc['filename']} ({doc['page_count']} pages)")


# ══════════════════════════════════════════════════════════════
# status — Show instance state
# ══════════════════════════════════════════════════════════════


@main.command()
@click.option("--prompts", is_flag=True, help="List all AI-generated prompts (summary)")
@click.option(
    "--prompt", "prompt_key", default=None, help="Show full text of a specific prompt by key"
)
@click.option("--prompt-list", "prompt_list", is_flag=True, help="List prompt keys only")
@click.option("--offset", default=0, help="Character offset for --prompt output (default: 0)")
@click.option(
    "--length", "length", default=0, help="Max characters to show for --prompt (0 = full)"
)
@click.pass_context
def status(
    ctx: click.Context,
    prompts: bool,
    prompt_key: str | None,
    prompt_list: bool,
    offset: int,
    length: int,
) -> None:
    """Show instance state — bootstrapped, domain, ontology versions, prompts.

    \b
    Examples:
      synapse status                          # general overview
      synapse status --prompt-list            # just the prompt keys
      synapse status --prompts                # list all generated prompts (summary)
      synapse status --prompt reasoning_system  # show full reasoning_system prompt
      synapse status --prompt reasoning_system --offset 500 --length 1000
    """
    settings: Settings = ctx.obj["settings"]
    store = settings.get_instance_store()

    # ── List prompt keys only ─────────────────────────────────
    if prompt_list:
        active_vid = store.get_active_version_id()
        if active_vid is None:
            click.echo("No active ontology version. Run 'synapse init' first.")
            store.close()
            return
        all_prompts = store.get_all_prompts(active_vid)
        if not all_prompts:
            click.echo("No prompts stored.")
        else:
            for key in sorted(all_prompts.keys()):
                click.echo(key)
        store.close()
        return

    # ── Show a specific prompt ────────────────────────────────
    if prompt_key is not None:
        active_vid = store.get_active_version_id()
        if active_vid is None:
            click.echo("No active ontology version. Run 'synapse init' first.")
            store.close()
            return

        text = store.get_prompt(prompt_key, active_vid)
        if text is None:
            all_prompts = store.get_all_prompts(active_vid)
            click.echo(f"Prompt '{prompt_key}' not found.")
            if all_prompts:
                click.echo(f"Available keys: {', '.join(sorted(all_prompts.keys()))}")
            store.close()
            return

        total_len = len(text)
        start = min(offset, total_len)
        if length > 0:
            end = min(start + length, total_len)
        else:
            end = total_len

        click.echo(f"=== {prompt_key} ({total_len} chars) ===")
        if start > 0 or end < total_len:
            click.echo(f"    showing chars {start}-{end} of {total_len}")
        click.echo()
        click.echo(text[start:end])

        store.close()
        return

    # ── List all prompts (summary) ────────────────────────────
    if prompts:
        active_vid = store.get_active_version_id()
        if active_vid is None:
            click.echo("No active ontology version. Run 'synapse init' first.")
            store.close()
            return

        all_prompts = store.get_all_prompts(active_vid)
        if not all_prompts:
            click.echo("No prompts stored.")
            store.close()
            return

        click.echo(f"AI-Generated Prompts (version {active_vid}, {len(all_prompts)} total)")
        click.echo("=" * 60)
        for key in sorted(all_prompts.keys()):
            text = all_prompts[key]
            # First line or first 120 chars as preview
            preview = text.replace("\n", " ").strip()[:120]
            click.echo(f"\n  {key}  ({len(text)} chars)")
            click.echo(f"    {preview}...")

        click.echo(f"\nTo view full prompt: synapse status --prompt <key>")
        store.close()
        return

    # ── Default: general status ───────────────────────────────
    click.echo("SynapseOS3 Instance Status")
    click.echo("=" * 40)
    click.echo(f"Instance dir: {settings.get_instance_dir()}")
    click.echo(f"Graph name: {settings.graph_name}")
    click.echo(f"Bootstrapped: {store.is_bootstrapped()}")

    if store.is_bootstrapped():
        click.echo(f"Domain: {store.get_meta('domain')}")
        click.echo(f"Subdomain: {store.get_meta('subdomain')}")
        click.echo(f"Language: {store.get_meta('language')}")
        click.echo(f"Bootstrap time: {store.get_meta('bootstrap_timestamp')}")

        active_vid = store.get_active_version_id()
        if active_vid:
            etypes = store.get_entity_types(active_vid)
            rtypes = store.get_relationship_types(active_vid)
            all_prompts = store.get_all_prompts(active_vid)
            click.echo(f"\nActive ontology version: {active_vid}")
            click.echo(f"  Entity types: {len(etypes)}")
            click.echo(f"  Relationship types: {len(rtypes)}")
            click.echo(f"  Prompts: {len(all_prompts)}")

            if etypes:
                click.echo("\n  Entity types:")
                for etype, desc in sorted(etypes.items()):
                    click.echo(f"    {etype}: {desc}")
            if rtypes:
                click.echo("\n  Relationship types:")
                for rtype, desc in sorted(rtypes.items()):
                    click.echo(f"    {rtype}: {desc}")

        sources = store.get_bootstrap_sources()
        if sources:
            click.echo(f"\n  Bootstrap sources ({len(sources)}):")
            for src in sources:
                click.echo(f"    {src['source_path']} ({src['page_count']} pages)")

    versions = store.list_versions()
    if versions:
        click.echo(f"\nAll ontology versions ({len(versions)}):")
        for v in versions:
            active = " (ACTIVE)" if v["is_active"] else ""
            click.echo(f"  v{v['version_id']}: {v['name']} — {v['domain']}{active}")

    store.close()


# ══════════════════════════════════════════════════════════════
# versions — List/switch ontology versions
# ══════════════════════════════════════════════════════════════


@main.command()
@click.option("--activate", type=int, default=None, help="Activate a specific version")
@click.option("--export", "export_id", type=int, default=None, help="Export a version to JSON")
@click.pass_context
def versions(ctx: click.Context, activate: int | None, export_id: int | None) -> None:
    """List and manage ontology versions."""
    settings: Settings = ctx.obj["settings"]
    store = settings.get_instance_store()

    if activate is not None:
        store.activate_version(activate)
        click.echo(f"Activated ontology version {activate}")
        store.close()
        return

    if export_id is not None:
        data = store.export_version(export_id)
        if data:
            click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            click.echo(f"Version {export_id} not found.")
        store.close()
        return

    versions_list = store.list_versions()
    if not versions_list:
        click.echo("No ontology versions found. Run 'synapse init' first.")
    else:
        click.echo("Ontology Versions:")
        for v in versions_list:
            active = " << ACTIVE" if v["is_active"] else ""
            click.echo(
                f"  v{v['version_id']}: {v['name']} [{v['domain']}] ({v['created_at']}){active}"
            )

    store.close()


# ══════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════


def _resolve_pdf_paths(paths: tuple[str, ...]) -> list[str]:
    """Resolve paths to a list of PDF file paths."""
    import glob as globmod

    pdf_files: list[str] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            pdf_files.extend(str(f) for f in sorted(path.glob("*.pdf")))
            pdf_files.extend(str(f) for f in sorted(path.glob("*.PDF")))
        elif path.is_file() and path.suffix.lower() == ".pdf":
            pdf_files.append(str(path))
        else:
            matches = sorted(globmod.glob(p))
            matched = [m for m in matches if m.lower().endswith(".pdf")]
            if matched:
                pdf_files.extend(matched)
            else:
                click.echo(f"Warning: no PDF files matched '{p}'")

    # Deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for f in pdf_files:
        resolved = str(Path(f).resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(f)
    return unique


def _print_tree(sections: list, indent: int = 0) -> None:
    for section in sections:
        prefix = " " * indent
        title = section.get("title", "Unknown")
        node_id = section.get("node_id", "")
        click.echo(
            f"{prefix}[{node_id}] {title} (pp. {section.get('start_page', '?')}-{section.get('end_page', '?')})"
        )
        children = section.get("children", [])
        if children:
            _print_tree(children, indent + 2)


if __name__ == "__main__":
    main()
