# SynapseOS

Domain-agnostic knowledge operating system. Feed it any documents — it discovers the ontology, generates extraction prompts, builds a knowledge graph, and answers multi-hop reasoning questions. No code changes between domains.

## How It Works

A fresh instance knows nothing about your domain. It only has predefined general prompts that know how to:

1. Analyze documents to discover what entity/relationship types exist
2. Generate domain-specific extraction prompts from the discovered ontology

After bootstrap, the instance is specialized for your domain — but the engine is generic.

```
Fresh clone (no ontology, no prompts)
  │
  synapse init <docs>        # bootstrap: discover ontology + generate prompts
  │
  synapse ingest <docs>      # extract knowledge graph using generated ontology
  │
  synapse chat               # ask questions, get grounded answers
  │
  synapse ingest <more>      # grow the KG — no duplicates, same ontology
```

## Requirements

- Python 3.11+
- Docker (for FalkorDB)
- An OpenAI-compatible API key (OpenRouter, OpenAI, etc.)

## Installation

```bash
git clone git@github.com:DrEverr/synapseos.git
cd synapseos
pip install -e .
```

Start FalkorDB:

```bash
docker compose up -d
```

Configure your API key:

```bash
cp .env.example .env
# Edit .env — set SYNAPSE_LLM_API_KEY at minimum
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_LLM_API_KEY` | (required) | API key for your LLM provider |
| `SYNAPSE_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | LLM API base URL |
| `SYNAPSE_LLM_MODEL` | `openrouter/auto` | Model identifier |
| `SYNAPSE_LLM_TIMEOUT` | `180` | Request timeout in seconds |
| `SYNAPSE_FALKORDB_HOST` | `localhost` | FalkorDB host |
| `SYNAPSE_FALKORDB_PORT` | `6379` | FalkorDB port |
| `SYNAPSE_GRAPH_NAME` | `synapse_kg` | Graph name (use different names for different domains) |

## Usage

### 1. Bootstrap — Discover Ontology from Documents

```bash
# From a directory of PDFs
synapse init ./documents/

# From specific files
synapse init paper1.pdf paper2.pdf datasheet.pdf
```

This analyzes your documents and:
- Identifies the domain (e.g., "industrial chemistry", "contract law", "cardiology")
- Discovers entity types (e.g., `CHEMICAL_COMPOUND`, `LEGAL_CLAUSE`, `DIAGNOSIS`)
- Discovers relationship types (e.g., `REACTS_WITH`, `REFERENCES_CLAUSE`, `TREATS`)
- Generates 10 domain-specific prompts for extraction and reasoning
- Stores everything in a SQLite database (`~/.synapse/<graph_name>/instance.db`)

### 2. Ingest — Build the Knowledge Graph

```bash
synapse ingest ./documents/

# Reset graph and re-ingest from scratch
synapse ingest ./documents/ --reset

# Dry run — extract but don't store
synapse ingest paper.pdf --dry-run
```

### 3. Chat — Ask Questions

```bash
# Interactive mode
synapse chat

# Single question
synapse chat -q "What consensus mechanism does JAM use?"

# Verbose — see the reasoning trace
synapse chat -v -q "How does the refine phase work?"
```

The chat uses a ReAct reasoning loop that queries the knowledge graph with Cypher, reads document sections, and synthesizes answers grounded in your data.

### 4. Grow — Add More Documents

```bash
synapse ingest ./new-batch/
```

Entity resolution deduplicates across ingestion runs. The KG grows without duplicates.

## Inspection & Management

### Instance Status

```bash
synapse status                    # overview: domain, entity types, relationship types
synapse status --prompts          # list all AI-generated prompts with previews
synapse status --prompt-list      # just the prompt keys
synapse status --prompt <key>     # show full prompt text
synapse status --prompt <key> --offset 500 --length 1000  # slice of a prompt
```

### Knowledge Graph

```bash
synapse inspect                   # node/edge counts, entity/relationship type breakdown
synapse inspect --triples         # show all extracted triples
synapse inspect --tree            # show document section trees
synapse inspect --duplicates      # find duplicate entities
synapse inspect --cypher "MATCH (n:PROTOCOL)-[r]->(m) RETURN n.name, type(r), m.name"
```

### Ontology Versions

```bash
synapse versions                  # list all versions
synapse versions --activate 1     # switch to version 1
synapse versions --export 1       # export version as JSON (for backup or transfer)
```

## Running Multiple Domains

Each domain gets its own graph name and instance directory:

```bash
# Legal documents
SYNAPSE_GRAPH_NAME=legal synapse init ./legal-docs/
SYNAPSE_GRAPH_NAME=legal synapse ingest ./legal-docs/
SYNAPSE_GRAPH_NAME=legal synapse chat

# Medical papers
SYNAPSE_GRAPH_NAME=medical synapse init ./medical-papers/
SYNAPSE_GRAPH_NAME=medical synapse ingest ./medical-papers/
SYNAPSE_GRAPH_NAME=medical synapse chat
```

Instance data is stored at `~/.synapse/<graph_name>/`.

## Backup & Restore

Everything lives in one SQLite file per instance:

```bash
# Backup
cp ~/.synapse/my_kg/instance.db ~/backups/my_kg_backup.db

# Export ontology version as JSON
synapse versions --export 1 > ontology_v1.json
```

## Architecture

```
src/synapse/
├── bootstrap/       # Ontology discovery + prompt generation from documents
│   ├── prompts.py   # Predefined general prompts (the only hardcoded prompts)
│   └── pipeline.py  # Domain analysis → ontology discovery → refinement → prompt generation
├── extraction/      # Entity/relationship extraction using generated prompts
├── chat/            # ReAct reasoning loop, tree search, post-answer enrichment
├── storage/
│   ├── graph.py          # FalkorDB — knowledge graph CRUD
│   ├── instance_store.py # SQLite — ontologies, prompts, metadata (versioned)
│   └── text_cache.py     # File cache for section text
├── parsers/         # PDF extraction with TOC detection + structure analysis
├── resolution/      # Entity deduplication (normalization + fuzzy matching)
├── llm/             # Async OpenAI client, JSON repair, safe template substitution
├── models/          # Pydantic models: Document, Entity, Relationship
├── config.py        # Settings + OntologyRegistry (reads from SQLite after bootstrap)
└── cli.py           # Click CLI: init, ingest, chat, inspect, status, versions
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
