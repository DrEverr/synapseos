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
  synapse -g <domain> init <docs>        # bootstrap: discover ontology + generate prompts
  │
  synapse -g <domain> ingest <docs>      # extract knowledge graph using generated ontology
  │
  synapse -g <domain> chat               # ask questions, get grounded answers
  │
  synapse -g <domain> ingest <more>      # grow the KG — no duplicates, same ontology
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
| `SYNAPSE_GRAPH_NAME` | `synapse_kg` | Default graph name (overridden by `-g`) |

## Usage

The `-g` / `--graph` flag selects which domain to work with. Each domain gets its own ontology, prompts, and knowledge graph — completely isolated.

### 1. Bootstrap — Discover Ontology from Documents

```bash
# Initialize a new domain from a directory of PDFs
synapse -g electronics init ./datasheets/

# From specific files
synapse -g legal init contract1.pdf contract2.pdf terms.pdf
```

This analyzes your documents and:
- Identifies the domain (e.g., "industrial chemistry", "contract law", "cardiology")
- Discovers entity types (e.g., `CHEMICAL_COMPOUND`, `LEGAL_CLAUSE`, `DIAGNOSIS`)
- Discovers relationship types (e.g., `REACTS_WITH`, `REFERENCES_CLAUSE`, `TREATS`)
- Generates 10 domain-specific prompts for extraction and reasoning
- Stores everything in a SQLite database (`~/.synapse/<domain>/instance.db`)

### 2. Ingest — Build the Knowledge Graph

```bash
synapse -g electronics ingest ./datasheets/

# Reset graph and re-ingest from scratch
synapse -g electronics ingest ./datasheets/ --reset

# Dry run — extract but don't store
synapse -g electronics ingest datasheet.pdf --dry-run
```

### 3. Chat — Ask Questions

```bash
# Interactive mode
synapse -g electronics chat

# Single question
synapse -g electronics chat -q "What is the maximum operating temperature of the LM7805?"

# Verbose — see the reasoning trace
synapse -g electronics chat -v -q "Compare voltage regulators by dropout voltage"
```

The chat uses a ReAct reasoning loop that queries the knowledge graph with Cypher, reads document sections, and synthesizes answers grounded in your data.

### 4. Grow — Add More Documents

```bash
synapse -g electronics ingest ./new-datasheets/
```

Entity resolution deduplicates across ingestion runs. The KG grows without duplicates.

## Multi-Domain

Each `-g` name is a fully isolated domain with its own ontology, prompts, and graph. They share the same FalkorDB instance but never intersect:

```bash
# Domain 1: blockchain protocols
synapse -g jam init ./graypaper/
synapse -g jam ingest ./graypaper/
synapse -g jam chat -q "What consensus mechanism does JAM use?"

# Domain 2: electronics
synapse -g electronics init ./datasheets/
synapse -g electronics ingest ./datasheets/
synapse -g electronics chat -q "What is the pinout of the ATmega328P?"

# Domain 3: legal
synapse -g legal init ./contracts/
synapse -g legal ingest ./contracts/
synapse -g legal chat -q "What are the termination clauses?"

# Each has its own ontology, prompts, and knowledge graph
synapse -g jam status --prompts
synapse -g electronics status --prompts
synapse -g legal status --prompts
```

Instance data is stored at `~/.synapse/<domain>/`. If `-g` is omitted, falls back to `SYNAPSE_GRAPH_NAME` env var (default: `synapse_kg`).

## Inspection & Management

### Instance Status

```bash
synapse -g jam status                      # overview: domain, entity types, relationship types
synapse -g jam status --prompts            # list all AI-generated prompts with previews
synapse -g jam status --prompt-list        # just the prompt keys
synapse -g jam status --prompt <key>       # show full prompt text
synapse -g jam status --prompt <key> --offset 500 --length 1000  # slice of a prompt
```

### Knowledge Graph

```bash
synapse -g jam inspect                     # node/edge counts, entity/relationship type breakdown
synapse -g jam inspect --triples           # show all extracted triples
synapse -g jam inspect --tree              # show document section trees
synapse -g jam inspect --duplicates        # find duplicate entities
synapse -g jam inspect --cypher "MATCH (n:PROTOCOL)-[r]->(m) RETURN n.name, type(r), m.name"
```

### Ontology Versions

```bash
synapse -g jam versions                    # list all versions
synapse -g jam versions --activate 1       # switch to version 1
synapse -g jam versions --export 1         # export version as JSON (for backup or transfer)
```

## Backup & Restore

Everything lives in one SQLite file per domain:

```bash
# Backup
cp ~/.synapse/jam/instance.db ~/backups/jam_backup.db

# Export ontology version as JSON
synapse -g jam versions --export 1 > ontology_v1.json
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
