# Selfix — Phase 4: Observability, CLI & Packaging

> **Goal:** Make Selfix debuggable, operable, and distributable.  
> A pipeline that works but cannot be observed, operated from the command line,  
> or installed by others is a prototype. Phase 4 graduates Selfix into a real product.

---

## 1. Phase 4 Scope

### Builds On
Phase 3 delivered a production-capable autonomous pipeline with remote repos, signals, and PR creation.  
Phase 4 adds the operational layer: observability, CLI, and packaging.

### In Scope
- LangSmith tracing — every node, tool call, and agent decision traced
- Structured event log — machine-readable event stream per pipeline run
- Selfix event hooks — caller-injectable callbacks at key pipeline events
- `selfix` CLI — `run`, `watch`, `status`, `history`, `replay` commands
- `pyproject.toml` — proper packaging with optional dependency groups
- PyPI publication workflow
- Human-readable console output (rich terminal UI)
- Run history store — queryable log of all past runs

### Out of Scope (deferred)
- Multi-repo parallel execution → Phase 5
- Signal queuing with backpressure → Phase 5

---

## 2. LangSmith Tracing

LangSmith is the native observability layer for LangGraph. Every node execution, every Claude Agent SDK call, every tool invocation is captured as a trace.

### 2.1 Configuration

```python
# selfix/observability/tracing.py

def configure_tracing(config: TracingConfig) -> None:
    """
    Call once at startup to enable LangSmith tracing.
    All subsequent LangGraph and Claude SDK calls are automatically traced.
    """
    if not config.enabled:
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = config.api_key
    os.environ["LANGCHAIN_PROJECT"] = config.project_name or "selfix"
    os.environ["LANGCHAIN_ENDPOINT"] = config.endpoint or "https://api.smith.langchain.com"

@dataclass
class TracingConfig:
    enabled: bool = False
    api_key: str | None = None
    project_name: str = "selfix"
    endpoint: str | None = None
```

Since LangGraph has native LangSmith integration, no manual instrumentation is needed inside nodes. Enabling `LANGCHAIN_TRACING_V2` is sufficient. Every `graph.ainvoke()` call automatically produces a trace with:

- Node execution timeline
- Input/output state at each node
- All Claude Agent SDK calls (prompts, tool calls, responses)
- Token usage per call
- Latency per node and total pipeline duration

### 2.2 Custom trace metadata

Selfix injects signal metadata into each trace for filtering in LangSmith UI:

```python
# in selfix/graph/orchestrator.py — ainvoke call

await graph.ainvoke(
    initial_state,
    config={
        "configurable": {"thread_id": signal.id},
        "metadata": {
            "selfix.signal_type": type(signal).__name__,
            "selfix.signal_id": signal.id,
            "selfix.repo": config.repo_config.url if config.repo_config else config.repo_path,
            "selfix.max_attempts": config.max_attempts,
        },
        "tags": ["selfix", type(signal).__name__.lower()],
    }
)
```

This enables LangSmith queries like:
- *Show all traces for ErrorSignal runs in the last 7 days*
- *Show all runs that escalated*
- *Compare token usage between attempt 1 and attempt 2 for this run*

---

## 3. Structured Event Log

LangSmith is for debugging. The structured event log is for integration — feeding downstream systems like Slack, PagerDuty, custom dashboards, or audit systems.

### 3.1 SelfixEvent

```python
# selfix/observability/events.py

@dataclass
class SelfixEvent:
    event_type: Literal[
        "run.started",
        "run.exploration_complete",
        "run.attempt_started",
        "run.attempt_complete",
        "run.validation_passed",
        "run.validation_failed",
        "run.pr_created",
        "run.escalated",
        "run.completed",
        "run.error",
    ]
    run_id: str              # = signal.id
    signal_type: str
    signal_description: str
    attempt_number: int | None
    timestamp: datetime
    data: dict               # event-specific payload
```

### 3.2 Event handler protocol

```python
class SelfixEventHandler(Protocol):
    async def on_event(self, event: SelfixEvent) -> None:
        ...
```

Built-in handlers provided:

```python
# selfix/observability/handlers/

JsonFileEventHandler      # writes NDJSON to a file
ConsoleEventHandler       # pretty-prints to stdout
SlackEventHandler         # posts key events to a Slack webhook
CompositeEventHandler     # fans out to multiple handlers
```

### 3.3 Example: JsonFileEventHandler

```python
@dataclass
class JsonFileEventHandler:
    output_path: str   # e.g. ".selfix/events/run-abc123.ndjson"

    async def on_event(self, event: SelfixEvent) -> None:
        with open(self.output_path, "a") as f:
            f.write(json.dumps(asdict(event)) + "\n")
```

Each line in the NDJSON file is one event. A complete successful run produces events in order:

```
{"event_type": "run.started", "run_id": "abc123", ...}
{"event_type": "run.exploration_complete", ...}
{"event_type": "run.attempt_started", "attempt_number": 1, ...}
{"event_type": "run.validation_failed", "attempt_number": 1, ...}
{"event_type": "run.attempt_started", "attempt_number": 2, ...}
{"event_type": "run.validation_passed", "attempt_number": 2, ...}
{"event_type": "run.pr_created", "data": {"pr_url": "...", "pr_number": 42}, ...}
{"event_type": "run.completed", ...}
```

### 3.4 Wiring event emission into graph nodes

Rather than cluttering every node with event emission logic, a LangGraph node wrapper handles it:

```python
# selfix/graph/instrumented_node.py

def instrumented(event_type_prefix: str, node_fn):
    """
    Wraps a node function to emit started/complete events automatically.
    """
    async def wrapper(state: PipelineState) -> dict:
        await emit(SelfixEvent(
            event_type=f"{event_type_prefix}.started",
            run_id=state["signal"].id,
            ...
        ))
        result = await node_fn(state)
        await emit(SelfixEvent(
            event_type=f"{event_type_prefix}.complete",
            ...
        ))
        return result
    return wrapper
```

---

## 4. Run History Store

A local queryable store of all past Selfix runs. Backed by SQLite.

```python
# selfix/observability/history.py

class RunHistoryStore:

    def __init__(self, db_path: str = ".selfix/history.db"):
        self.db_path = db_path
        self._init_db()

    def record(self, result: SelfixResult) -> None:
        """Called at pipeline completion to persist the run summary."""
        ...

    def list_runs(
        self,
        status: str | None = None,
        signal_type: str | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[RunSummary]:
        """Query past runs with optional filters."""
        ...

    def get_run(self, run_id: str) -> RunSummary | None:
        """Fetch a single run by ID."""
        ...

@dataclass
class RunSummary:
    run_id: str
    signal_type: str
    signal_description: str
    status: str
    attempts: int
    pr_url: str | None
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
```

---

## 5. CLI

The `selfix` CLI makes the tool operable without writing Python.  
Built with [Typer](https://typer.tiangolo.com/) for clean argument handling and [Rich](https://rich.readthedocs.io/) for terminal output.

### 5.1 Command surface

```
selfix run        — trigger a manual pipeline run
selfix watch      — start the webhook listener
selfix status     — show status of a specific run (with live polling)
selfix history    — list past runs
selfix replay     — re-run a past signal
selfix config     — validate a selfix config file
```

### 5.2 `selfix run`

```bash
selfix run \
  --repo /path/to/repo \
  --signal "The cache in src/cache.py causes memory leaks under high load" \
  --validator "pytest tests/ -x -q" \
  --max-attempts 3 \
  --build-cmd "mypy src/"

# With a config file:
selfix run --config selfix.yaml
```

### 5.3 `selfix watch`

```bash
selfix watch \
  --config selfix.yaml \
  --port 8765 \
  --secret $WEBHOOK_SECRET

# Output:
# ✓ Selfix webhook listener started on :8765
# Registered endpoints:
#   POST /signal/error
#   POST /signal/metric
#   POST /signal/manual
#   POST /webhook/sentry
#   POST /webhook/datadog
#   POST /webhook/github
```

### 5.4 `selfix status`

```bash
selfix status abc123

# Output:
# Run: abc123
# Signal: ManualSignal — "Improve cache eviction in src/cache.py"
# Status: running
# Current node: fix_generation (attempt 2/3)
# Started: 2026-04-01 14:32:11 (2m 14s ago)
#
# Attempt 1: ❌ failed
#   Feedback: "Tests pass but memory usage is still 2.1GB at 10k connections"
```

### 5.5 `selfix history`

```bash
selfix history --limit 10 --status success

# Output (table):
# ID       Signal Type    Description                      Status     Attempts  PR
# abc123   ManualSignal   Improve cache eviction...        success    2         #142
# def456   ErrorSignal    NullPointerException in...       success    1         #139
# ghi789   MetricSignal   p99 latency regressed to 340ms   escalated  3         —
```

### 5.6 `selfix replay`

```bash
selfix replay abc123

# Re-runs the signal from run abc123 from scratch (new run_id).
# Useful for re-triggering an escalated run after manual context changes.
```

### 5.7 `selfix.yaml` — config file format

```yaml
# selfix.yaml

repo:
  url: https://github.com/myorg/myservice
  local_path: /tmp/selfix/myservice
  auth_token: ${GITHUB_TOKEN}

agent:
  model: claude-opus-4-6
  max_tokens: 8192

pipeline:
  max_attempts: 3
  build_command: "mypy src/ --strict"
  checkpoint_dir: .selfix/checkpoints

validator:
  type: shell
  command: "pytest tests/ -x -q"

pr:
  base_branch: main
  labels: [selfix, automated]
  draft: true
  reviewers: [lead-dev]

observability:
  langsmith:
    enabled: true
    api_key: ${LANGSMITH_API_KEY}
    project: selfix-prod
  events:
    handlers:
      - type: json_file
        path: .selfix/events/
      - type: slack
        webhook_url: ${SLACK_WEBHOOK_URL}
        on_events: [run.pr_created, run.escalated]
```

---

## 6. Rich Terminal Output

When running interactively (TTY detected), Selfix renders a live pipeline progress UI using Rich.

```
╭─ Selfix Pipeline ────────────────────────────────────────────────╮
│ Signal:  ManualSignal                                            │
│ Repo:    /projects/myapp                                         │
│ Run ID:  abc123                                                  │
╰──────────────────────────────────────────────────────────────────╯

  ✓  signal_intake       0.1s
  ✓  repo_setup          0.3s  (branch: selfix/fix-abc123)
  ✓  exploration         8.2s  (12 files read, 3 relevant)
  ●  fix_generation      ...   (attempt 1/3)
     Claude is editing src/cache.py...

  ○  build_check
  ○  validation
  ○  pr_creation
```

On completion:

```
  ✓  signal_intake       0.1s
  ✓  repo_setup          0.3s
  ✓  exploration         8.2s
  ✓  fix_generation      14.1s  (attempt 1)  ❌  validation failed
  ✓  fix_generation      11.3s  (attempt 2)  ✅  validation passed
  ✓  pr_creation         1.2s

╭─ Result ─────────────────────────────────────────────────────────╮
│ Status:   ✅ SUCCESS                                              │
│ Attempts: 2/3                                                    │
│ PR:       https://github.com/myorg/myapp/pull/142               │
│ Duration: 43.7s                                                  │
╰──────────────────────────────────────────────────────────────────╯
```

When not a TTY (CI, piped), plain structured log output is emitted instead.

---

## 7. Package Structure (Phase 4 complete)

```
selfix/
├── __init__.py
├── config.py
├── result.py
├── signals/
│   ├── base.py
│   ├── error.py
│   ├── metric.py
│   ├── scheduled.py
│   ├── manual.py
│   ├── router.py
│   └── webhook.py
├── validator/
│   ├── protocol.py
│   └── builtin/
│       ├── shell.py
│       ├── pytest.py
│       ├── composite.py
│       └── http.py
├── graph/
│   ├── orchestrator.py
│   ├── state.py
│   ├── instrumented_node.py
│   └── nodes/
│       ├── signal_intake.py
│       ├── repo_setup.py
│       ├── exploration.py
│       ├── fix_generation.py
│       ├── build_check.py
│       ├── validation.py
│       ├── retry_decision.py
│       ├── pr_creation.py
│       └── escalation.py
├── agent/
│   ├── worker.py
│   └── prompts.py
├── git/
│   ├── repo.py
│   ├── pr.py
│   └── providers/
│       ├── github.py
│       └── gitlab.py
├── observability/
│   ├── tracing.py
│   ├── events.py
│   ├── history.py
│   └── handlers/
│       ├── json_file.py
│       ├── console.py
│       ├── slack.py
│       └── composite.py
└── cli/
    ├── main.py          # typer app entry point
    ├── run.py
    ├── watch.py
    ├── status.py
    ├── history.py
    ├── replay.py
    └── ui.py            # rich rendering
```

---

## 8. pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "selfix"
version = "0.1.0"
description = "Autonomous code improvement pipeline powered by LangGraph and Claude"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }

dependencies = [
    "langgraph>=1.0.0",
    "anthropic>=0.40.0",
    "pydantic>=2.0.0",
    "gitpython>=3.1.0",
    "aiohttp>=3.9.0",
    "typer>=0.12.0",
    "rich>=13.0.0",
    "aiosqlite>=0.20.0",
]

[project.optional-dependencies]
langsmith = ["langsmith>=0.1.0"]
all = ["selfix[langsmith]"]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "mypy>=1.9.0",
    "ruff>=0.4.0",
]

[project.scripts]
selfix = "selfix.cli.main:app"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
strict = true
python_version = "3.11"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

---

## 9. Phase 4 Deliverables

| # | Deliverable | Description |
|---|---|---|
| D1 | LangSmith tracing | Enabled via `TracingConfig`; metadata and tags per run |
| D2 | `SelfixEvent` + event handler protocol | Structured event stream |
| D3 | `JsonFileEventHandler`, `SlackEventHandler` | Built-in event handlers |
| D4 | `RunHistoryStore` | SQLite-backed queryable run history |
| D5 | `selfix run` CLI command | Trigger pipeline from terminal |
| D6 | `selfix watch` CLI command | Start webhook listener from terminal |
| D7 | `selfix status` CLI command | Live run status with polling |
| D8 | `selfix history` CLI command | Tabular run history |
| D9 | `selfix replay` CLI command | Re-run a past signal |
| D10 | `selfix.yaml` config file support | YAML config with env var interpolation |
| D11 | Rich terminal UI | Live progress rendering |
| D12 | `pyproject.toml` | Proper packaging with optional groups |
| D13 | PyPI publish workflow | GitHub Actions workflow for release |
| D14 | Full documentation | README, usage guide, validator authoring guide |

---

## 10. Phase 4 Success Criteria

1. `pip install selfix` installs successfully on Python 3.11+
2. `selfix run --repo . --signal "..." --validator "pytest"` completes a full run from terminal
3. LangSmith traces appear in the configured project with correct metadata and tags
4. `selfix history` lists past runs from the SQLite store
5. `selfix status <run-id>` polls and displays live node progress
6. A Slack event handler posts `run.pr_created` and `run.escalated` events correctly
7. `selfix.yaml` config file is parsed and validated with clear errors on misconfiguration
8. All Phase 1–3 success criteria still pass

---

## 11. What Phase 5 Adds

Phase 4 ends with a fully packaged, observable, CLI-operable tool. Phase 5 introduces:

- **Multi-repo support** — run Selfix across a portfolio of repos simultaneously
- **Signal queue with backpressure** — queue signals, process concurrently with worker pool
- **Parallel fix attempts** — try multiple fix strategies simultaneously, take the first that passes
- **Custom agent config per signal type** — different models/budgets for error vs scheduled scans
- **Repo registry** — register repos with Selfix once, reference by name in signals

---

*Document version: 0.1 — Phase 4 design*  
*Status: Draft*  
*Depends on: Phase-3.md*
