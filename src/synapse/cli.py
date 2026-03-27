"""CLI entry points for SynapseOS: init, ingest, chat, inspect, status."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import click

from synapse.config import OntologyRegistry, Settings, get_settings


def setup_logging(level: str = "INFO") -> None:
    from datetime import datetime

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter

    # Console handler — respects user-requested level
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    root.addHandler(console)

    # File handler — always DEBUG, one file per run
    logs_dir = Path.home() / ".synapse" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(logs_dir / f"synapse_{timestamp}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)


@click.group()
@click.option(
    "-g", "--graph", default=None, help="Graph/domain name (overrides SYNAPSE_GRAPH_NAME)"
)
@click.option("--log-level", default=None, help="Logging level")
@click.version_option(None, "-V", "--version", package_name="synapseos", prog_name="synapse")
@click.pass_context
def main(ctx: click.Context, graph: str | None, log_level: str | None) -> None:
    """SynapseOS — Domain-Agnostic Knowledge Operating System.

    \b
    Lifecycle:
      1. synapse -g <domain> init <docs>    Bootstrap a new domain
      2. synapse -g <domain> ingest <docs>  Extract KG using generated ontology
      3. synapse -g <domain> chat           Chat with the knowledge graph
      4. synapse -g <domain> ingest <more>  Grow the KG with new documents

    \b
    Management:
      synapse -g <domain> status            Show instance state
      synapse -g <domain> inspect           Inspect the knowledge graph
      synapse -g <domain> versions          List ontology versions

    \b
    The -g/--graph flag selects which domain to work with. Each domain
    gets its own ontology, prompts, and knowledge graph. If omitted,
    falls back to SYNAPSE_GRAPH_NAME env var (default: synapse_kg).
    """
    ctx.ensure_object(dict)
    settings = get_settings()
    if graph:
        settings.graph_name = graph
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

    pdf_files = _resolve_document_paths(paths)
    if not pdf_files:
        click.echo("Error: no supported documents found.")
        sys.exit(1)

    store = settings.get_instance_store()

    if store.is_bootstrapped():
        click.echo("Warning: This instance has already been bootstrapped.")
        click.echo(f"  Domain: {store.get_meta('domain')}")
        click.echo(f"  Bootstrapped: {store.get_meta('bootstrap_timestamp')}")
        if not click.confirm("Re-bootstrap? (creates a new ontology version)"):
            return

    click.echo(f"Bootstrapping from {len(pdf_files)} document(s):")
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

    pdf_files = _resolve_document_paths(paths)
    if not pdf_files:
        click.echo("Error: no supported documents found.")
        sys.exit(1)

    click.echo(f"Ingesting {len(pdf_files)} document(s):")
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
@click.option("--resume", "-r", is_flag=True, help="Resume last chat session")
@click.option("--session", "-s", default=None, help="Resume a named session")
@click.option("--stream", is_flag=True, help="Stream reasoning steps in real-time")
@click.option("--debate", is_flag=True, help="Enable multi-agent debate for answer verification")
@click.pass_context
def chat(ctx: click.Context, query: str | None, verbose: bool, resume: bool, session: str | None, stream: bool, debate: bool) -> None:
    """Chat with the knowledge graph using multi-hop reasoning."""
    settings: Settings = ctx.obj["settings"]

    if not settings.llm_api_key:
        click.echo("Error: SYNAPSE_LLM_API_KEY is not set.")
        sys.exit(1)

    store = settings.get_instance_store()
    ontology = OntologyRegistry(store=store, ontology_name=settings.ontology)

    from synapse.llm.client import LLMClient
    from synapse.storage.graph import GraphStore
    from synapse.storage.text_cache import TextCache

    chat_model = settings.chat_model or settings.llm_model
    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=chat_model,
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

    from synapse.chat.reasoning import compact_history, reason_full

    # ── Session setup: resume existing or create new ──────────
    chat_history: list[dict[str, object]] = []
    cached_summary = ""
    compacted_turns = 0

    resumed_session = None
    if session:
        resumed_session = store.get_session_by_name(session)
        if not resumed_session:
            click.echo(f"Error: No session named '{session}'. Use /sessions to list.")
            sys.exit(1)
    elif resume:
        resumed_session = store.get_last_session()
        if not resumed_session:
            click.echo("No previous session found. Starting a new one.")

    if resumed_session:
        session_id = resumed_session["session_id"]
        session_name = resumed_session.get("name", "")
        cached_summary = resumed_session.get("summary", "") or ""
        compacted_turns = resumed_session.get("compacted_turns", 0) or 0
        # Rebuild chat_history from stored episodes
        episodes = store.get_session_episodes(session_id)
        for ep in episodes:
            chat_history.append({
                "question": ep["question"],
                "answer": ep["answer"],
                "actions_log": json.loads(ep["actions_log"]) if isinstance(ep["actions_log"], str) else ep["actions_log"],
                "section_ids": json.loads(ep["section_ids"]) if isinstance(ep["section_ids"], str) else ep["section_ids"],
            })
        label = f"'{session_name}'" if session_name else session_id[:8]
        click.echo(f"Resumed session {label} ({len(episodes)} previous turns)")
    else:
        session_id = str(uuid.uuid4())
        session_name = ""
        # Session created lazily on first question via _ensure_session()

    _session_created = bool(resumed_session)

    def _ensure_session() -> None:
        """Create the session in DB on first use (lazy)."""
        nonlocal _session_created
        if not _session_created:
            store.create_session(session_id, domain=domain)
            _session_created = True

    # Compaction LLM client (cheap/fast model)
    compaction_model = settings.compaction_model
    compaction_llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=compaction_model,
        timeout=settings.llm_timeout,
    )

    def _run_compaction() -> None:
        """Compact older turns into a summary using the compaction LLM."""
        nonlocal cached_summary, compacted_turns
        uncompacted_count = len(chat_history) - compacted_turns
        if uncompacted_count <= settings.compaction_threshold_turns:
            return
        # Compact all but the last 2 turns (keep recent ones full)
        turns_to_compact = chat_history[compacted_turns:-2] if len(chat_history) > 2 else []
        if not turns_to_compact:
            return
        click.echo("  [compacting conversation context...]")
        new_summary = asyncio.run(
            compact_history(turns_to_compact, compaction_llm, cached_summary)
        )
        if new_summary:
            new_compacted = len(chat_history) - 2
            cached_summary = new_summary
            compacted_turns = new_compacted
            store.update_session_summary(session_id, cached_summary, compacted_turns)
            click.echo(f"  [compacted {new_compacted} turns into summary]")

    if query:
        _ensure_session()
        result = asyncio.run(
            reason_full(
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
                session_id=session_id,
                cached_summary=cached_summary,
                compacted_turns=compacted_turns,
                context_max_tokens=settings.chat_context_max_tokens,
                stream=stream,
                debate=debate,
                debate_max_rounds=settings.debate_max_rounds,
                debate_confidence_threshold=settings.debate_confidence_threshold,
            )
        )
        click.echo(f"\nAnswer: {result.answer}")
    else:
        label = f"'{session_name}'" if session_name else session_id[:8]
        click.echo(f"SynapseOS Chat — {domain} ({node_count} nodes) [session {label}]")
        click.echo("Type 'quit' to exit. Commands: /name <n>, /sessions, /history, /compact")
        click.echo("-" * 40)
        # Show previous turns for resumed sessions
        if chat_history:
            for turn in chat_history:
                compact_marker = ""
                click.echo(f"\nYou{compact_marker}: {turn['question']}")
                click.echo(f"\nSynapseOS: {turn['answer']}")
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                click.echo("\nBye!")
                break
            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                click.echo("Bye!")
                break

            # ── Slash commands ────────────────────────────
            if user_input.startswith("/name "):
                new_name = user_input[6:].strip()
                if new_name:
                    store.rename_session(session_id, new_name)
                    session_name = new_name
                    click.echo(f"Session named '{new_name}'")
                else:
                    click.echo("Usage: /name <session-name>")
                continue

            if user_input == "/sessions":
                sessions = store.list_sessions()
                if not sessions:
                    click.echo("No sessions found.")
                else:
                    for s in sessions:
                        name_part = f" '{s['name']}'" if s.get("name") else ""
                        active = " (current)" if s["session_id"] == session_id else ""
                        click.echo(
                            f"  {s['session_id'][:8]}{name_part} — "
                            f"{s['episode_count']} turns — {s['started_at'][:16]}{active}"
                        )
                continue

            if user_input == "/history":
                if not chat_history:
                    click.echo("No turns in this session yet.")
                else:
                    for i, turn in enumerate(chat_history, 1):
                        q = turn.get("question", "")
                        a = str(turn.get("answer", ""))
                        compact_marker = " (compacted)" if i <= compacted_turns else ""
                        click.echo(f"  [{i}]{compact_marker} Q: {q}")
                        click.echo(f"      A: {a[:120]}{'...' if len(a) > 120 else ''}")
                continue

            if user_input == "/compact":
                _run_compaction()
                continue

            _ensure_session()
            result = asyncio.run(
                reason_full(
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
                    chat_history=chat_history,
                    session_id=session_id,
                    cached_summary=cached_summary,
                    compacted_turns=compacted_turns,
                    context_max_tokens=settings.chat_context_max_tokens,
                    stream=stream,
                debate=debate,
                debate_max_rounds=settings.debate_max_rounds,
                debate_confidence_threshold=settings.debate_confidence_threshold,
                )
            )
            chat_history.append({
                "question": user_input,
                "answer": result.answer,
                "actions_log": result.actions_log,
                "section_ids": result.section_ids_used,
            })
            click.echo(f"\nSynapseOS: {result.answer}")

            # Auto-name session after first turn (if unnamed)
            if len(chat_history) == 1 and not session_name:
                try:
                    auto_name = asyncio.run(
                        compaction_llm.complete(
                            system="Generate a short session name (2-5 words, lowercase, no quotes) "
                                   "that captures the topic of this question. Reply with ONLY the name.",
                            user=user_input,
                            temperature=0.0,
                            max_tokens=20,
                        )
                    )
                    auto_name = auto_name.strip().strip("\"'").lower()
                    if auto_name:
                        store.rename_session(session_id, auto_name)
                        session_name = auto_name
                        click.echo(f"  [session: '{auto_name}']")
                except Exception:
                    pass  # non-critical, skip silently

            # Auto-compact when threshold exceeded
            _run_compaction()

    store.close()


# ══════════════════════════════════════════════════════════════
# inspect — Inspect the knowledge graph
# ══════════════════════════════════════════════════════════════


@main.command()
@click.option("--duplicates", is_flag=True, help="Show duplicates")
@click.option("--triples", is_flag=True, help="Show all triples")
@click.option("--tree", is_flag=True, help="Show document trees")
@click.option("--cypher", default=None, help="Execute Cypher query")
@click.option("--unverified", is_flag=True, help="Show unverified (AI-generated) entities and relationships")
@click.option("--limit", default=100, help="Limit results")
@click.pass_context
def inspect(
    ctx: click.Context, duplicates: bool, triples: bool, tree: bool, cypher: str | None, unverified: bool, limit: int
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

    if unverified:
        entities = graph.get_unverified_entities()
        rels = graph.get_unverified_relationships()
        click.echo(f"Unverified entities ({len(entities)}):")
        for row in entities:
            click.echo(f"  [{row[1]}] {row[0]}  (confidence: {row[2]}, source: {row[3]})")
        click.echo(f"\nUnverified relationships ({len(rels)}):")
        for row in rels:
            click.echo(f"  {row[0]} -[{row[1]}]-> {row[2]}  (confidence: {row[3]}, source: {row[4]})")
        if not entities and not rels:
            click.echo("  All items are verified!")
        return

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
    click.echo("SynapseOS Knowledge Graph")
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
    click.echo("SynapseOS Instance Status")
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


@main.command(name="export")
@click.argument("session")
@click.option("--format", "fmt", type=click.Choice(["md", "pdf"]), default="md", help="Output format")
@click.option("--output", "-o", default=None, help="Output file path (default: stdout for md, required for pdf)")
@click.pass_context
def export_session(ctx: click.Context, session: str, fmt: str, output: str | None) -> None:
    """Export a chat session to Markdown or PDF.

    SESSION can be a session name, full ID, or ID prefix.

    Examples:

      synapse -g cooking export "pizza session" --format md

      synapse -g cooking export e77bdaab -o session.md

      synapse -g cooking export "pizza session" --format pdf -o report.pdf
    """
    settings: Settings = ctx.obj["settings"]
    store = settings.get_instance_store()

    from synapse.export import export_session_to_markdown, export_session_to_pdf

    try:
        if fmt == "md":
            md = export_session_to_markdown(session, store)
            if output:
                Path(output).write_text(md, encoding="utf-8")
                click.echo(f"Exported to {output}")
            else:
                click.echo(md)
        elif fmt == "pdf":
            if not output:
                click.echo("Error: --output is required for PDF export.")
                sys.exit(1)
            export_session_to_pdf(session, store, output)
            click.echo(f"Exported to {output}")
    except ValueError as e:
        click.echo(f"Error: {e}")
        sys.exit(1)
    except ImportError as e:
        click.echo(f"Error: {e}")
        sys.exit(1)
    finally:
        store.close()


# ══════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════


def _resolve_document_paths(paths: tuple[str, ...]) -> list[str]:
    """Resolve paths to a list of supported document files (PDF, Markdown, text, HTML, email)."""
    import glob as globmod

    from synapse.parsers import SUPPORTED_EXTENSIONS, is_supported

    files: list[str] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for ext in SUPPORTED_EXTENSIONS:
                files.extend(str(f) for f in sorted(path.glob(f"*{ext}")))
                files.extend(str(f) for f in sorted(path.glob(f"*{ext.upper()}")))
        elif path.is_file() and is_supported(str(path)):
            files.append(str(path))
        elif path.is_file():
            click.echo(f"Warning: unsupported file type '{path.suffix}' — skipping '{p}'")
        else:
            matches = sorted(globmod.glob(p))
            matched = [m for m in matches if is_supported(m)]
            if matched:
                files.extend(matched)
            else:
                click.echo(f"Warning: no supported documents matched '{p}'")

    # Deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for f in files:
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
