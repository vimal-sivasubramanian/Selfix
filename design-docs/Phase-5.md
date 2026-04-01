# Selfix — Phase 5: Scale, Multi-Repo & Extensibility

> **Goal:** Take Selfix from a single-repo tool to a fleet-level autonomous improvement system.  
> Phase 5 introduces concurrent execution across multiple repos, signal queuing with backpressure,  
> parallel fix strategies, and the extensibility primitives that let teams build on top of Selfix.

---

## 1. Phase 5 Scope

### Builds On
Phase 4 delivered a packaged, observable, CLI-operable autonomous pipeline for a single repo.  
Phase 5 makes Selfix operate at scale: many repos, many signals, many concurrent runs.

### In Scope
- Repo registry — register and manage a portfolio of repos
- Signal queue — persistent queue with concurrency control and backpressure
- Worker pool — concurrent pipeline execution with configurable parallelism
- Parallel fix strategies — multiple Claude agent approaches run simultaneously per signal
- Custom agent config per signal type — different models and budgets per signal category
- `selfix.yaml` fleet config — define a portfolio of repos and their validators
- `selfix fleet` CLI commands — manage and observe multi-repo operations
- Plugin system — first-class extension points for custom nodes, signal adapters, and validators
- Resource budgets — token spend limits per run, per day, per repo

### Out of Scope
- Cloud-hosted Selfix service (out of scope for the open-source package)
- Web UI dashboard (out of scope — CLI + LangSmith is the observability layer)

---

## 2. Repo Registry

A central registry of all repos Selfix knows about. Stored in SQLite alongside the run history.

```python
# selfix/registry/repo_registry.py

@dataclass
class RegisteredRepo:
    name: str                           # short alias, e.g. "api-service"
    repo_config: RepoConfig             # URL, local path, auth
    default_validator: SelfixValidator  # used if signal doesn't specify one
    default_build_command: str | None
    default_agent_config: AgentConfig | None
    signal_configs: dict[str, SignalTypeConfig]  # per signal-type overrides
    tags: list[str]                     # e.g. ["production", "python", "critical"]
    active: bool = True

class RepoRegistry:

    def register(self, repo: RegisteredRepo) -> None: ...
    def get(self, name: str) -> RegisteredRepo | None: ...
    def list(self, tags: list[str] | None = None) -> list[RegisteredRepo]: ...
    def deactivate(self, name: str) -> None: ...
```

### Fleet config file (`selfix-fleet.yaml`)

```yaml
# selfix-fleet.yaml

defaults:
  max_attempts: 3
  agent:
    model: claude-opus-4-6
  pr:
    base_branch: main
    draft: true
    labels: [selfix, automated]

repos:
  - name: api-service
    url: https://github.com/myorg/api-service
    local_path: /tmp/selfix/api-service
    tags: [production, python]
    validator:
      type: composite
      validators:
        - type: shell
          command: "pytest tests/ -x -q --cov=src --cov-fail-under=80"
        - type: http
          start_command: "uvicorn app:main --port 8080"
          health_url: "http://localhost:8080/health"
    build_command: "mypy src/ --strict"
    signal_configs:
      error:
        max_attempts: 5
        agent:
          model: claude-opus-4-6
          max_tokens: 16384
      scheduled:
        max_attempts: 2
        agent:
          model: claude-sonnet-4-6   # cheaper for routine scans

  - name: algo-trading
    url: https://github.com/myorg/algo
    local_path: /tmp/selfix/algo
    tags: [trading, python]
    validator:
      type: shell
      command: "python backtest.py --assert-min-sharpe 1.2 --assert-max-drawdown 0.15"
      timeout: 600

  - name: go-service
    url: https://github.com/myorg/goservice
    local_path: /tmp/selfix/goservice
    tags: [production, go]
    validator:
      type: shell
      command: "go test ./... -race"
    build_command: "go build ./..."
```

---

## 3. Signal Queue

Phase 1–4 processed signals synchronously — one at a time. Phase 5 introduces an async queue that decouples signal receipt from pipeline execution.

```
External Sources
  │
  ▼
┌──────────────────┐
│   Signal Queue   │  ← persistent SQLite-backed queue
│   (backpressure) │
└────────┬─────────┘
         │  dequeues N signals concurrently
         ▼
┌──────────────────┐
│   Worker Pool    │  ← N concurrent pipeline runners
│   (N workers)    │
└──────────────────┘
```

### 3.1 SignalQueue

```python
# selfix/queue/signal_queue.py

class SignalQueue:
    """
    Persistent, ordered queue of signals waiting to be processed.
    Backed by SQLite. Survives process restarts.
    """

    def __init__(self, db_path: str, max_size: int = 1000):
        self.db_path = db_path
        self.max_size = max_size

    async def enqueue(self, signal: Signal, repo_name: str) -> str:
        """
        Add a signal to the queue.
        Returns queue_id.
        Raises QueueFullError if queue is at max_size (backpressure).
        """
        ...

    async def dequeue(self) -> QueuedSignal | None:
        """
        Pop the next signal from the queue (FIFO within priority).
        Returns None if queue is empty.
        Marks the item as "processing" — if processing fails,
        it is returned to the queue after a timeout.
        """
        ...

    async def complete(self, queue_id: str) -> None:
        """Mark a queued signal as successfully processed."""
        ...

    async def fail(self, queue_id: str, error: str) -> None:
        """
        Mark a queued signal as failed.
        Increments retry_count. If retry_count > max_retries, marks as dead-lettered.
        """
        ...

    async def dead_letters(self) -> list[QueuedSignal]:
        """Returns signals that have exceeded max retries."""
        ...

@dataclass
class QueuedSignal:
    queue_id: str
    signal: Signal
    repo_name: str
    enqueued_at: datetime
    priority: int = 0         # higher = processed first
    retry_count: int = 0
    max_retries: int = 2
```

### 3.2 Priority

Signals are prioritised in the queue:

| Signal Type | Default Priority |
|---|---|
| `ErrorSignal` with `frequency > 100` | 10 (highest) |
| `ErrorSignal` | 8 |
| `MetricSignal` | 6 |
| `ManualSignal` | 5 |
| `ScheduledSignal` | 2 (lowest) |

Priority is configurable per signal type in the fleet config.

---

## 4. Worker Pool

```python
# selfix/queue/worker_pool.py

class WorkerPool:
    """
    Runs N concurrent Selfix pipeline workers.
    Each worker dequeues a signal, runs the pipeline, marks complete or failed.
    """

    def __init__(
        self,
        queue: SignalQueue,
        registry: RepoRegistry,
        concurrency: int = 3,
        budget: ResourceBudget | None = None,
    ):
        self.queue = queue
        self.registry = registry
        self.concurrency = concurrency
        self.budget = budget
        self._running: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Start the worker pool. Runs until stopped."""
        semaphore = asyncio.Semaphore(self.concurrency)
        while True:
            item = await self.queue.dequeue()
            if item is None:
                await asyncio.sleep(1)
                continue

            async with semaphore:
                task = asyncio.create_task(self._process(item))
                self._running[item.queue_id] = task

    async def _process(self, item: QueuedSignal) -> None:
        repo = self.registry.get(item.repo_name)
        if not repo:
            await self.queue.fail(item.queue_id, f"Repo '{item.repo_name}' not found in registry")
            return

        config = self._build_config(repo, item.signal)

        try:
            if self.budget and not self.budget.can_run(item.repo_name):
                await self.queue.fail(item.queue_id, "Daily token budget exceeded")
                return

            result = await selfix.run(config)
            await self.queue.complete(item.queue_id)

            if self.budget:
                self.budget.record_run(item.repo_name, result)

        except Exception as e:
            await self.queue.fail(item.queue_id, str(e))
        finally:
            self._running.pop(item.queue_id, None)
```

---

## 5. Parallel Fix Strategies

A major capability unlock. Instead of one fix attempt at a time, Selfix can spawn multiple Claude agents with different strategies simultaneously and take the first that passes validation.

```
fix_generation (attempt N)
  │
  ├── strategy: "minimal" — smallest possible change to fix the issue
  ├── strategy: "refactor" — fix + improve surrounding code
  └── strategy: "algorithmic" — rethink the algorithm entirely
  │
  (run all 3 concurrently)
  │
  ▼
validation (all 3 results)
  │
  ├── take first that passes
  └── if none pass → feedback injection from best-scoring → retry
```

### 5.1 ParallelFixConfig

```python
@dataclass
class ParallelFixConfig:
    strategies: list[FixStrategy]
    selection: Literal["first_passing", "highest_score"] = "first_passing"

@dataclass
class FixStrategy:
    name: str
    prompt_modifier: str    # appended to the fix_generation prompt
    agent_config: AgentConfig | None = None  # optional per-strategy model override

# Example:
parallel_fix = ParallelFixConfig(
    strategies=[
        FixStrategy(
            name="minimal",
            prompt_modifier="Make the smallest change that fixes the issue. Touch as few lines as possible.",
        ),
        FixStrategy(
            name="refactor",
            prompt_modifier="Fix the issue and improve the surrounding code quality.",
        ),
        FixStrategy(
            name="algorithmic",
            prompt_modifier="Rethink the algorithm from scratch if needed. Prioritise correctness and performance.",
            agent_config=AgentConfig(model="claude-opus-4-6", max_tokens=16384),
        ),
    ]
)
```

### 5.2 Parallel fix node

```python
async def parallel_fix_generation_node(state: PipelineState) -> dict:
    parallel_config = state["config"].parallel_fix_config
    if not parallel_config:
        # Fall back to standard single-strategy fix
        return await fix_generation_node(state)

    # Clone the repo working tree for each strategy
    strategy_paths = await clone_for_strategies(
        state["repo_path"],
        len(parallel_config.strategies),
    )

    # Run all strategies concurrently
    tasks = [
        run_strategy(state, strategy, path)
        for strategy, path in zip(parallel_config.strategies, strategy_paths)
    ]
    strategy_results: list[StrategyResult] = await asyncio.gather(*tasks)

    # Validate all results concurrently
    validation_tasks = [
        state["config"].validator.validate(r.repo_path, build_context(state, r))
        for r in strategy_results
    ]
    validation_results = await asyncio.gather(*validation_tasks)

    # Select winner
    winner = select_winner(
        strategy_results,
        validation_results,
        mode=parallel_config.selection,
    )

    if winner:
        # Apply the winning diff to the actual repo path
        await apply_diff(state["repo_path"], winner.diff)
        return {
            "fix_diff": winner.diff,
            "agent_reasoning": winner.reasoning,
            "validation_result": winner.validation_result,
        }
    else:
        # No strategy passed — use best-scoring feedback for retry
        best = max(zip(strategy_results, validation_results), key=lambda x: x[1].score)
        return {
            "fix_diff": best[0].diff,
            "agent_reasoning": best[0].reasoning,
            "validation_result": best[1],
        }
```

---

## 6. Custom Agent Config Per Signal Type

Different signals warrant different model configurations. An `ErrorSignal` from production at 2am deserves the most capable model. A routine `ScheduledSignal` nightly scan can use a faster, cheaper model.

```python
@dataclass
class SignalTypeConfig:
    """Per-signal-type overrides for agent and pipeline config."""
    max_attempts: int | None = None
    agent_config: AgentConfig | None = None
    parallel_fix_config: ParallelFixConfig | None = None
    validator: SelfixValidator | None = None   # override default repo validator

# In RegisteredRepo:
signal_configs: dict[str, SignalTypeConfig] = field(default_factory=dict)
# Keys: "error", "metric", "scheduled", "manual"

# Resolution order:
# 1. signal_configs[signal_type] (most specific)
# 2. RegisteredRepo defaults
# 3. SelfixConfig defaults
# 4. Global defaults
```

---

## 7. Resource Budgets

Cost control for fleet-scale operation.

```python
# selfix/queue/budget.py

@dataclass
class ResourceBudget:
    """
    Limits token spend to prevent runaway costs.
    All limits are per calendar day (UTC).
    """
    daily_token_limit: int | None = None         # total tokens across all runs
    daily_token_limit_per_repo: int | None = None
    max_concurrent_runs: int = 3
    max_runs_per_repo_per_day: int | None = None

    def can_run(self, repo_name: str) -> bool:
        """Returns True if budget allows another run for this repo."""
        ...

    def record_run(self, repo_name: str, result: SelfixResult) -> None:
        """Called after a run completes to record token usage."""
        ...

    def daily_summary(self) -> BudgetSummary:
        """Returns today's spend summary across all repos."""
        ...
```

---

## 8. Plugin System

Phase 5 formalises the extension points so teams can publish and share Selfix plugins.

### 8.1 Extension points

```python
# selfix/plugins/protocol.py

class SelfixPlugin(Protocol):
    """
    A plugin can contribute any combination of:
    - Custom signal adapters (new signal sources)
    - Custom validators
    - Custom node implementations
    - Custom event handlers
    - Custom PR providers
    """
    name: str
    version: str

    def register(self, registry: PluginRegistry) -> None:
        """
        Called at startup. Register contributions into the plugin registry.
        """
        ...

class PluginRegistry:
    def add_signal_adapter(self, name: str, adapter: SignalAdapter) -> None: ...
    def add_validator(self, name: str, factory: ValidatorFactory) -> None: ...
    def add_node(self, name: str, node_fn: NodeFn, position: NodePosition) -> None: ...
    def add_event_handler(self, handler: SelfixEventHandler) -> None: ...
    def add_pr_provider(self, name: str, provider: PRProvider) -> None: ...
```

### 8.2 Example plugin — Datadog metrics adapter

```python
# selfix-datadog/selfix_datadog/__init__.py

class DatadogPlugin:
    name = "selfix-datadog"
    version = "1.0.0"

    def __init__(self, api_key: str, app_key: str):
        self.api_key = api_key
        self.app_key = app_key

    def register(self, registry: PluginRegistry) -> None:
        registry.add_signal_adapter("datadog", DatadogSignalAdapter(
            api_key=self.api_key,
            app_key=self.app_key,
        ))
        registry.add_event_handler(DatadogMetricsHandler(
            api_key=self.api_key,
        ))

# Usage:
selfix.plugins.register(DatadogPlugin(
    api_key=os.environ["DD_API_KEY"],
    app_key=os.environ["DD_APP_KEY"],
))
```

### 8.3 Plugin discovery

Plugins registered as Python entry points are auto-discovered:

```toml
# In a plugin's pyproject.toml:
[project.entry-points."selfix.plugins"]
datadog = "selfix_datadog:DatadogPlugin"
```

On startup, Selfix scans installed packages for the `selfix.plugins` entry point group and auto-registers them.

---

## 9. Fleet CLI Commands

```
selfix fleet register    — add a repo to the registry
selfix fleet list        — list all registered repos
selfix fleet status      — show live status of all running pipelines
selfix fleet queue       — show signal queue depth and dead letters
selfix fleet start       — start the worker pool
selfix fleet budget      — show today's token spend across all repos
```

### `selfix fleet status`

```
Fleet Status — 2026-04-01 14:32:11

Workers: 2/3 active

RUNNING
  api-service    fix_generation  attempt 1/3   ErrorSignal   "NullPointer in UserService"   1m 12s
  algo-trading   validation      attempt 2/3   ManualSignal  "Improve Sharpe ratio"         4m 03s

QUEUE (8 signals pending)
  Priority 8  ErrorSignal    go-service     "panic: index out of range"
  Priority 6  MetricSignal   api-service    "p99 latency regressed to 420ms"
  Priority 2  ScheduledSignal  *all repos*  "nightly security scan"
  ...

RECENT (last 5)
  ✅  api-service    PR #142   ManualSignal     2 attempts   43s
  ✅  go-service     PR #89    ErrorSignal      1 attempt    28s
  ❌  algo-trading   escalated  MetricSignal    3 attempts   12m
```

### `selfix fleet start`

```bash
selfix fleet start \
  --config selfix-fleet.yaml \
  --concurrency 3 \
  --daily-token-budget 2000000

# Output:
# ✓ Loaded 4 repos from registry
# ✓ Worker pool started (3 concurrent workers)
# ✓ Webhook listener on :8765
# ✓ Daily token budget: 2,000,000 tokens
# Waiting for signals...
```

---

## 10. Phase 5 Deliverables

| # | Deliverable | Description |
|---|---|---|
| D1 | `RepoRegistry` | SQLite-backed portfolio of repos with per-repo config |
| D2 | `SignalQueue` | Persistent queue with priority, backpressure, dead letters |
| D3 | `WorkerPool` | Concurrent pipeline execution with semaphore control |
| D4 | `ParallelFixConfig` | Multiple fix strategies run concurrently per attempt |
| D5 | `SignalTypeConfig` | Per-signal-type model and pipeline overrides |
| D6 | `ResourceBudget` | Token spend limits per run, per repo, per day |
| D7 | `PluginRegistry` | Extension point registration and auto-discovery |
| D8 | `selfix-fleet.yaml` config format | Full fleet config with repos, validators, budgets |
| D9 | `selfix fleet` CLI commands | Register, list, start, status, queue, budget |
| D10 | Example plugin: `selfix-datadog` | Reference plugin implementation |
| D11 | Load and stress tests | 10 concurrent runs across 5 repos without state corruption |
| D12 | Plugin authoring guide | Documentation for building and publishing Selfix plugins |

---

## 11. Phase 5 Success Criteria

1. `selfix fleet start` runs 3 concurrent pipelines across different repos without state corruption
2. Signal queue correctly prioritises `ErrorSignal` over `ScheduledSignal`
3. Parallel fix strategies: all 3 strategies run concurrently, winner is applied to repo
4. `SignalTypeConfig` correctly selects `claude-sonnet-4-6` for scheduled scans and `claude-opus-4-6` for errors
5. `ResourceBudget` correctly blocks new runs after daily token limit is reached
6. A custom plugin registered via entry point is auto-discovered and loaded at startup
7. Dead-lettered signals appear in `selfix fleet queue` output
8. All Phase 1–4 success criteria still pass

---

## 12. Overall Delivery Summary

| Phase | Core Capability | Key Abstraction Introduced |
|---|---|---|
| 1 | Single-pass pipeline, local repo, manual signal | `SelfixValidator` Protocol |
| 2 | Retry loop, feedback injection, persistent state | `AttemptRecord`, feedback cycle |
| 3 | Remote repos, all signal types, PR creation | `SignalRouter`, `PRProvider` |
| 4 | Observability, CLI, packaging | `SelfixEvent`, `RunHistoryStore` |
| 5 | Multi-repo fleet, concurrency, plugins | `RepoRegistry`, `WorkerPool`, `PluginRegistry` |

---

*Document version: 0.1 — Phase 5 design*  
*Status: Draft*  
*Depends on: Phase-4.md*
