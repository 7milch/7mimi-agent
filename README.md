# 7mimi Agent

MCP-first autonomous research agent inspired by Mercari Engineering's remote-claude / pcp-agent architecture.

Documentation starts here:

- [docs/README.md](docs/README.md)

Related generated notes repository:

- https://github.com/nishiog/ai-it-research-notes

## Current implementation status

The first implementation slice provides:

- Python package skeleton
- YAML config loader and validator
- SQLite schema / migration
- deterministic policy engine skeleton
- redaction and path policy helpers
- AI/IT daily digest dry-run runner using mock signals

## Development commands

Run commands from the repository root:

```bash
PYTHONPATH=src python3 -m sevenmimi_agent config validate
PYTHONPATH=src python3 -m sevenmimi_agent db init
PYTHONPATH=src python3 -m sevenmimi_agent schedule list
PYTHONPATH=src python3 -m sevenmimi_agent run-job ai-it-x-daily-digest --dry-run
```

Dry-run output is written under `.data/dry-run/` and is gitignored.

Run tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
