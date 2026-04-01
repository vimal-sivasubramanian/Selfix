# Selfix

**Selfix** is a language-agnostic, autonomous code improvement pipeline.

It watches for signals, uses a Claude AI agent to explore and fix a repository, validates the result against caller-injected criteria, and reports the outcome — entirely without human intervention.

> Phase 1: manual signal, shell validator, single local repo, single fix attempt.

---

## How it works

```
Signal → LangGraph orchestrator → Claude agent (explore + fix) → Validator → Result
```

1. You construct a `ManualSignal` describing what to fix
2. You provide a `ShellCommandValidator` (any command that exits 0 on success)
3. Selfix creates a `selfix/fix-*` branch, runs Claude to explore and edit files, then runs your validator
4. A `SelfixResult` is returned with the diff, Claude's reasoning, and the validation outcome

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

### Python repo — pytest validation

```python
import selfix
from selfix.signals import ManualSignal
from selfix.validator.builtin import ShellCommandValidator
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
    validator=ShellCommandValidator("pytest tests/ -x -q"),
))

print(result.status)           # "success" or "failed"
print(result.diff)             # unified diff of all changes
print(result.agent_reasoning)  # Claude's explanation
print(result.branch_name)      # "selfix/fix-<id>-<ts>"
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
        "python backtest.py --strategy momentum --assert-min-sharpe 1.2 --assert-max-drawdown 0.15"
    ),
))
```

---

## SelfixResult fields

| Field | Description |
|---|---|
| `status` | `"success"` / `"failed"` / `"escalated"` |
| `signal` | The original signal |
| `attempts` | Number of fix attempts made |
| `diff` | Unified diff of all changes |
| `validation_result` | `passed`, `score`, `feedback`, `metadata` |
| `agent_reasoning` | Claude's explanation of what it changed and why |
| `branch_name` | The `selfix/fix-*` branch created |
| `error` | Set if the pipeline itself errored |

---

## Custom validator

Any object with a matching `validate()` signature works — no import from Selfix required:

```python
class MyValidator:
    async def validate(self, repo_path: str, context) -> ...:
        # run your checks
        return ValidationResult(
            passed=True,
            score=1.42,
            feedback="Sharpe 1.42, drawdown 12% — all thresholds met",
        )
```

---

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| Phase 1 | **Current** | ManualSignal, ShellCommandValidator, single attempt |
| Phase 2 | Planned | Retry loop, feedback injection, Pytest/Composite validators |
| Phase 3 | Planned | Error/Metric/Cron signals, PR creation, escalation |
| Phase 4 | Planned | Observability, LangSmith tracing, CLI |
| Phase 5 | Planned | Multi-repo, parallel signal queuing |
