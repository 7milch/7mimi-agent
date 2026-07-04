# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the repository root with `PYTHONPATH=src`:

```bash
# Validate config/*.yaml consistency
PYTHONPATH=src python3 -m sevenmimi_agent config validate

# Initialize / migrate SQLite DB (.data/normalized/app.sqlite)
PYTHONPATH=src python3 -m sevenmimi_agent db init

# List scheduled jobs from config/schedules.yaml
PYTHONPATH=src python3 -m sevenmimi_agent schedule list

# Run a job (dry-run output goes to .data/dry-run/, gitignored)
PYTHONPATH=src python3 -m sevenmimi_agent run-job ai-it-x-daily-digest --dry-run

# Run the same job inside the isolated Docker container runner
PYTHONPATH=src python3 -m sevenmimi_agent run-job ai-it-x-daily-digest --dry-run --runner container

# Build the agent-runner container image
docker build -f Dockerfile.agent-runner -t 7mimi-agent-runner:latest .

# All tests
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Single test
PYTHONPATH=src python3 -m unittest tests.test_foundation.FoundationTest.test_config_validates -v

# Go proxy service tests
cd services/claude-proxy && go test ./...
cd services/auth-proxy && go test ./...

# Go proxy images (build context is each service dir)
docker build -f services/claude-proxy/Dockerfile -t 7mimi-claude-proxy:latest services/claude-proxy
docker build -f services/auth-proxy/Dockerfile -t 7mimi-auth-proxy:latest services/auth-proxy
```

Only runtime dependency is PyYAML. Tests use stdlib `unittest` (pytest config exists in pyproject.toml but tests are unittest-style).

## Architecture

MCP-first autonomous research agent (inspired by Mercari's remote-claude / pcp-agent). It collects AI/IT topics and Japanese stock signals from X, fact-checks them, and writes Markdown digests — generated notes go to a separate repo (`nishiog/ai-it-research-notes`), never into this repo.

### Config is the source of truth

Runtime behavior is driven by three YAML files, loaded via `config/loader.py` into a frozen `AppConfig` and cross-validated by `config/validator.py`:

- `config/roles.yaml` — role definitions (orchestrator, x_collector, stock_researcher, document_writer, source_verifier, ai_it_topic_runner) with system rules and output contracts.
- `config/policy.yaml` — deterministic security policy: per-role tool allow/deny lists (`role_tool_policy`), MCP server tool allowlists, document-repo path policies, redaction patterns. Security is enforced outside the LLM.
- `config/schedules.yaml` — cron jobs (Asia/Tokyo) and X query sets; jobs reference roles by name.

### Execution flow

`cli.py run-job` → creates session + task rows in SQLite (`db/repository.py`, schema in `db/schema.sql`) and a per-session workspace under `.sessions/` → dispatches to a `RunnerBackend` (`runner/backend.py` Protocol):

- `LocalRunnerBackend` — runs in-process.
- `ContainerRunnerBackend` — re-invokes the CLI (`runner-execute` subcommand) inside a Docker container with `--network none`, mounting the repo at `/workspace`, and forwards **only** an env allowlist (session id, role, proxy URLs/session tokens — never provider credentials).

Both paths converge on `execute_runner_task`, which currently only supports `ai_it_topic_runner` (`roles/ai_it_topic_runner.py`, mock signal collection for now).

### Security boundary (central design invariant)

- Polyglot split (ADR-012): Python owns orchestration/scheduler/research/Markdown generation; **Go owns the proxy boundary services** in `services/claude-proxy` (Claude API reverse proxy, credential injection, audit) and `services/auth-proxy` (`POST /v1/tool/authorize`, role/tool allowlist). `sevenmimi_agent/proxies/` holds the Python clients — `AuthProxyClient` is fail-closed when `AUTH_PROXY_URL` is set but unreachable, and only falls back to the local `PolicyEngine` in local/dev mode (no `AUTH_PROXY_URL`).
- agent-runner never holds real credentials. `ANTHROPIC_API_KEY` belongs to claude-proxy only; X/J-Quants/GitHub creds belong to auth-proxy/MCP servers. Never mount these into runner containers. Proxies log metadata only — never credentials or request bodies.
- Every tool call passes through the hook boundary: `hooks/pre_tool_use.py` (fail-**closed**, blocks via `security/policy_engine.py` role/tool/path checks) and `hooks/post_tool_use.py` (fail-**open**, best-effort audit to SQLite).
- `security/path_policy.py` enforces the allowed/denied path globs from `document_repositories` in policy.yaml (e.g. no writes to `.github/**`, `.env`, `secrets/**` in the notes repo).
- X posts are signals, never evidence; investment advice and X write operations are prohibited by policy.

### Spec-driven development

Docs under `docs/` are the spec; implement according to them, not ad hoc. Before implementing, check the relevant sections of `docs/architecture/`, `docs/detailed-design/`, `docs/workflows/` and the latest ADRs in `docs/planning/adr.md`. If a recent ADR contradicts the older design docs, **update the design docs first** to reflect the ADR, then implement against the updated docs. Never write code that follows neither — if neither the docs nor an ADR covers a decision you need, record the ADR, update the docs, then implement.

### ADR discipline (enforced by Stop hook)

Any change that alters architecture, security boundaries, language/tooling choices, or platform policy **must** be recorded as an ADR in `docs/planning/adr.md` **in the same work session** (append-only, numbered sequentially after the last existing ADR: `### ADR-NNN: <title>` with `Decision:` and `Reason:`). This applies to changes under `docs/architecture/`, `docs/detailed-design/`, `docs/workflows/`, and `config/*.yaml`. A Stop hook (`.claude/hooks/adr-check.sh`) blocks completion when those paths changed without an `adr.md` update — either add the ADR, or state explicitly to the user why no ADR is needed (typo fix, already covered by an existing ADR). Update existing ADRs rather than duplicating when a prior decision is revised.

### Documentation

Design docs live in `docs/` (Japanese): `docs/README.md` is the entry point; ADRs are appended to `docs/planning/adr.md`. Placeholder packages (`orchestrator/`, `scheduler/`, `proxies/`, `metrics/`, `tools/`) correspond to planned phases in `docs/planning/roadmap.md`.
