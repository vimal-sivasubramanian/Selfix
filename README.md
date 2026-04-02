# Selfix

**Selfix** is a language-agnostic, autonomous code improvement pipeline.

It watches for signals, uses a Claude AI agent to explore and fix a repository, validates the result against caller-injected criteria, and — if validation fails — retries with feedback injected into the next attempt. When all attempts are exhausted it escalates and leaves the branch intact for inspection.

> Phase 2: retry loop, feedback injection, persistent checkpointing, build gate, PytestValidator, CompositeValidator, HttpHealthValidator, escalation reports.

---

## How it works

```
Signal → LangGraph orchestrator → Claude agent (explore + fix) → build_check → Validator
                                        ▲                                           │
                                        └──── retry with feedback ◄─── FAILED ─────┤
                                                                                    │
                                        Result ◄─────────────── PASSED ────────────┘
```

1. You construct a `ManualSignal` describing what to fix
2. You provide a validator (shell command, pytest, composite, or custom)
3. Selfix creates a `selfix/fix-*` branch, runs Claude to explore and edit files
4. The result is validated; on failure the agent retries with the validator's feedback
5. After `max_attempts` the pipeline escalates and writes a report to the branch
6. A `SelfixResult` is returned with the diff, reasoning, validation outcome, and full attempt history

---

## Install

```bash
pip install selfix
# or
uv add selfix
```

Requires an `ANTHROPIC_API_KEY` environment variable.

---

## Usage

### Basic — pytest validation with 3 retry attempts

```python
import selfix
from selfix.signals import ManualSignal
from selfix.validator.builtin import PytestValidator
from selfix.config import SelfixConfig

result = selfix.run_sync(SelfixConfig(
    repo_path="/home/user/projects/myapp",
    signal=ManualSignal(
        description="""
            The function `calculate_risk_score` in src/risk.py has O(n²) complexity
            due to nested loops. Improve its performance while keeping all tests passing.
        """,
        scope_hint="src/risk.py",
    ),
    validator=PytestValidator(test_path="tests/", min_coverage=0.80),
    max_attempts=3,
))

print(result.status)           # "success" / "failed" / "escalated"
print(result.diff)             # unified diff of final changes
print(result.agent_reasoning)  # Claude's explanation
print(result.attempts)         # how many attempts were made

for i, attempt in enumerate(result.attempt_history):
    print(f"Attempt {i+1}: passed={attempt.validation_result.passed}")
    print(f"  Feedback: {attempt.validation_result.feedback}")
```

### Build gate + composite validation

```python
from selfix.validator.builtin import CompositeValidator, HttpHealthValidator, PytestValidator

result = selfix.run_sync(SelfixConfig(
    repo_path="/projects/api",
    signal=ManualSignal(
        description="The /search endpoint returns 500 for queries with special characters."
    ),
    validator=CompositeValidator([
        PytestValidator(test_path="tests/", min_coverage=0.75),
        HttpHealthValidator(
            start_command="uvicorn app:main --port 8080",
            health_url="http://localhost:8080/health",
        ),
    ]),
    max_attempts=3,
    build_command="mypy src/ --strict",     # fast gate before the expensive validator
    escalation_handler=lambda event: notify_slack(event),
))
```

### Go repo — race detector

```python
result = selfix.run_sync(SelfixConfig(
    repo_path="/home/user/projects/goservice",
    signal=ManualSignal(
        description="Fix the race condition reported in pkg/cache/lru.go",
        scope_hint="pkg/cache/",
    ),
    validator=ShellCommandValidator("go test ./... -race"),
))
```

### Algorithmic trading — backtest assertion

```python
result = selfix.run_sync(SelfixConfig(
    repo_path="/home/user/projects/algo",
    signal=ManualSignal(
        description="""
            The momentum strategy in strategies/momentum.py has a Sharpe ratio of 0.8
            on the 2020-2024 SPY backtest. Improve the signal calculation or
            position sizing to increase it above 1.2 without exceeding 15% drawdown.
        """
    ),
    validator=ShellCommandValidator(
        "python backtest.py --strategy momentum --assert-min-sharpe 1.2 --assert-max-drawdown 0.15",
        timeout_seconds=600,
    ),
    max_attempts=3,
))
```

---

## SelfixConfig fields

| Field | Type | Description |
|---|---|---|
| `repo_path` | `str` | Absolute path to the local git repository |
| `signal` | `Signal` | What to fix (use `ManualSignal`) |
| `validator` | `SelfixValidator` | How to verify the fix worked |
| `max_attempts` | `int` | Retry limit (default: `3`) |
| `build_command` | `str \| None` | Optional fast gate run before validation (e.g. `"mypy src/"`) |
| `escalation_handler` | `async callable \| None` | Called with an `EscalationEvent` when all attempts fail |
| `checkpoint_dir` | `str` | Where to persist the SQLite checkpoint (default: `.selfix/checkpoints`) |
| `agent_config` | `AgentConfig` | Model and tool overrides |

---

## SelfixResult fields

| Field | Description |
|---|---|
| `status` | `"success"` / `"failed"` / `"escalated"` |
| `signal` | The original signal |
| `attempts` | Number of fix attempts made |
| `diff` | Unified diff of the final applied changes |
| `validation_result` | `passed`, `score`, `feedback`, `metadata` from the last attempt |
| `attempt_history` | List of `AttemptRecord` — full audit trail of every attempt |
| `agent_reasoning` | Claude's explanation from the final attempt |
| `branch_name` | The `selfix/fix-*` branch created |
| `error` | Set if the pipeline itself errored |

---

## Built-in validators

### `ShellCommandValidator`

Passes if the command exits with code 0. Simplest option.

```python
ShellCommandValidator("pytest tests/ -x -q", timeout_seconds=300)
ShellCommandValidator("go test ./... -race")
ShellCommandValidator("cargo test")
```

### `PytestValidator`

Runs pytest with optional coverage enforcement.

```python
PytestValidator(
    test_path="tests/",
    min_coverage=0.80,       # fail if coverage < 80%
    extra_args=["--strict-markers"],
    timeout_seconds=300,
)
```

### `CompositeValidator`

Runs multiple validators concurrently. Supports AND (`"all"`) or OR (`"any"`) logic.
Feedback from all validators is combined and injected into the next retry.

```python
CompositeValidator([validator_a, validator_b], mode="all")   # both must pass
CompositeValidator([validator_a, validator_b], mode="any")   # either passing is enough
```

### `HttpHealthValidator`

Starts a process, polls an HTTP endpoint until healthy, then tears it down.

```python
HttpHealthValidator(
    start_command="uvicorn app:main --port 8080",
    health_url="http://localhost:8080/health",
    expected_status=200,
    startup_timeout=30,
    request_timeout=10,
)
```

### Custom validator

Any object with a matching `validate()` signature works:

```python
class MyValidator:
    async def validate(self, repo_path: str, context) -> ValidationResult:
        # run your checks
        return ValidationResult(
            passed=True,
            score=1.42,
            feedback="Sharpe 1.42, drawdown 12% — all thresholds met",
        )
```

---

## Escalation

When all attempts fail, Selfix:

1. Writes `.selfix/escalation-report.md` to the fix branch summarising every attempt and its feedback
2. Commits the report so it's visible in the branch history
3. Calls `escalation_handler` if configured
4. Returns a `SelfixResult` with `status="escalated"` and the full `attempt_history`

The branch is preserved — never deleted — so you can inspect what was tried.

---

## Checkpointing

Pipeline state is persisted to SQLite at `checkpoint_dir` (default `.selfix/checkpoints/selfix.db`). Each run is keyed by `signal.id`. If the process crashes mid-run and you call `selfix.run_sync` again with the same signal, LangGraph resumes from the last completed node automatically.

---

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| Phase 1 | Done | ManualSignal, ShellCommandValidator, single attempt |
| Phase 2 | **Current** | Retry loop, feedback injection, build gate, Pytest/Composite/HttpHealth validators, escalation, SQLite checkpointing |
| Phase 3 | Planned | Error/Metric/Cron signals, remote repo cloning, PR creation via GitHub/GitLab |
| Phase 4 | Planned | Observability, LangSmith tracing, CLI |
| Phase 5 | Planned | Multi-repo, parallel signal queuing |
