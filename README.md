# Selfix

**Selfix** is a language-agnostic, autonomous code improvement pipeline.

It watches for signals, uses a Claude AI agent to explore and fix a repository, validates the result against caller-injected criteria, and — if validation fails — retries with feedback injected into the next attempt. When a fix passes, Selfix pushes the branch and opens a pull request automatically.

> Phase 3: `ErrorSignal`, `MetricSignal`, `ScheduledSignal`, `SignalRouter`, remote repo cloning, `GitHubPRProvider`, `GitLabPRProvider`, `SelfixWebhookServer`.

---

## How it works

```
Signal → LangGraph orchestrator → Claude agent (explore + fix) → build_check → Validator
                                        ▲                                           │
                                        └──── retry with feedback ◄─── FAILED ─────┤
                                                                                    │
                                     PR created ◄──────────── PASSED ──────────────┘
```

1. A signal fires (error, metric regression, cron, or manual)
2. You provide a validator (shell command, pytest, composite, or custom)
3. Selfix clones/syncs the repo and creates a `selfix/fix-*` branch
4. Claude explores the repo, edits files, and the result is validated
5. On failure the agent retries with the validator's feedback injected into the prompt
6. After `max_attempts` the pipeline escalates and writes a report to the branch
7. On success Selfix pushes the branch and opens a PR via GitHub or GitLab
8. A `SelfixResult` is returned with the diff, reasoning, PR URL, and full attempt history

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

### Basic — local repo, pytest validation

```python
import selfix
from selfix.signals import ManualSignal
from selfix.validator.builtin import PytestValidator
from selfix.config import SelfixConfig

result = selfix.run_sync(SelfixConfig(
    repo_path="/home/user/projects/myapp",
    signal=ManualSignal(
        description="""
            The function `calculate_risk_score` in src/risk.py has O(n²) complexity.
            Improve its performance while keeping all tests passing.
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
```

### Remote GitHub repo with PR creation

```python
import os
import selfix
from selfix.signals import ErrorSignal
from selfix.validator.builtin import PytestValidator
from selfix.git import RepoConfig, GitHubPRProvider
from selfix.config import SelfixConfig, PRConfig

result = await selfix.run(SelfixConfig(
    repo_config=RepoConfig(
        url="https://github.com/myorg/myservice",
        local_path="/tmp/selfix/myservice",
        auth_token=os.environ["GITHUB_TOKEN"],
    ),
    signal=ErrorSignal(
        description="NullPointerException in UserService.getProfile()",
        stack_trace="...",
        file_hint="src/services/UserService.java",
        error_type="NullPointerException",
        frequency=47,
        environment="production",
    ),
    validator=PytestValidator(test_path="tests/", min_coverage=0.80),
    build_command="./gradlew compileJava",
    max_attempts=3,
    pr_config=PRConfig(
        base_branch="main",
        labels=["selfix", "bug-fix", "automated"],
        reviewers=["senior-dev"],
        draft=True,
    ),
    pr_provider=GitHubPRProvider(token=os.environ["GITHUB_TOKEN"]),
    escalation_handler=lambda event: notify_slack(
        f"Selfix could not fix: {event.signal.description}. "
        f"Branch {event.branch_name} left for manual review."
    ),
))

print(f"PR opened: {result.pr_url}")
```

### Metric regression — latency spike

```python
from selfix.signals import MetricSignal

result = await selfix.run(SelfixConfig(
    repo_config=RepoConfig(
        url="https://github.com/myorg/api",
        local_path="/tmp/selfix/api",
        auth_token=os.environ["GITHUB_TOKEN"],
    ),
    signal=MetricSignal(
        description="p99 latency on /api/search regressed from 80ms to 340ms",
        metric_name="http.request.duration.p99",
        metric_path="/api/search",
        current_value=340.0,
        baseline_value=80.0,
        threshold=150.0,
        unit="ms",
    ),
    validator=ShellCommandValidator("pytest tests/perf/ -x -q"),
    pr_provider=GitHubPRProvider(token=os.environ["GITHUB_TOKEN"]),
))
```

### Webhook-driven — receive signals from Sentry / Datadog

```python
from selfix.signals import SignalRouter, SelfixWebhookServer
from selfix.git import RepoConfig, GitHubPRProvider
from selfix.validator.builtin import PytestValidator

router = SignalRouter(
    config_factory=lambda signal: SelfixConfig(
        repo_config=RepoConfig(
            url="https://github.com/myorg/myservice",
            local_path="/tmp/selfix/myservice",
            auth_token=os.environ["GITHUB_TOKEN"],
        ),
        signal=signal,
        validator=PytestValidator(),
        pr_provider=GitHubPRProvider(token=os.environ["GITHUB_TOKEN"]),
    )
)

server = SelfixWebhookServer(router, secret=os.environ["WEBHOOK_SECRET"])
await server.run(port=8765)
# Configure Sentry to POST to http://your-host:8765/webhook/sentry
# Configure Datadog to POST to http://your-host:8765/webhook/datadog
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
    build_command="mypy src/ --strict",
    escalation_handler=lambda event: notify_slack(event),
))
```

### Algorithmic trading — backtest assertion

```python
result = selfix.run_sync(SelfixConfig(
    repo_path="/home/user/projects/algo",
    signal=ManualSignal(
        description="""
            The momentum strategy has a Sharpe ratio of 0.8 on the 2020–2024 SPY backtest.
            Improve it above 1.2 without exceeding 15% drawdown.
        """
    ),
    validator=ShellCommandValidator(
        "python backtest.py --assert-min-sharpe 1.2 --assert-max-drawdown 0.15",
        timeout_seconds=600,
    ),
    max_attempts=3,
))

for i, attempt in enumerate(result.attempt_history):
    print(f"Attempt {i+1}: passed={attempt.validation_result.passed}")
    print(f"  Feedback: {attempt.validation_result.feedback}")
```

---

## Signal types

| Signal | Trigger | Key fields |
|---|---|---|
| `ManualSignal` | Explicit caller call | `description`, `scope_hint` |
| `ErrorSignal` | Exception / stack trace / Sentry | `error_type`, `file_hint`, `line_hint`, `stack_trace`, `frequency` |
| `MetricSignal` | Metric regression / Datadog alert | `metric_name`, `current_value`, `baseline_value`, `threshold`, `unit` |
| `ScheduledSignal` | Cron / proactive scan | `cron`, `improvement_type` |

All signals carry `id` (UUID), `created_at`, `description`, and `scope_hint`.

---

## SelfixConfig fields

| Field | Type | Description |
|---|---|---|
| `repo_path` | `str \| None` | Local path to the git repository |
| `repo_config` | `RepoConfig \| None` | Remote repo — URL, auth token, clone depth |
| `signal` | `Signal` | What to fix |
| `validator` | `SelfixValidator` | How to verify the fix |
| `max_attempts` | `int` | Retry limit (default: `3`) |
| `build_command` | `str \| None` | Optional fast gate before validation (e.g. `"mypy src/"`) |
| `pr_config` | `PRConfig` | PR title, labels, reviewers, draft flag |
| `pr_provider` | `PRProvider \| None` | `GitHubPRProvider` or `GitLabPRProvider` — required for PR creation |
| `escalation_handler` | `async callable \| None` | Called with `EscalationEvent` when all attempts fail |
| `checkpoint_dir` | `str` | SQLite checkpoint path (default: `.selfix/checkpoints`) |
| `agent_config` | `AgentConfig` | Model and tool overrides |

At least one of `repo_path` or `repo_config` must be set.

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
| `pr_url` | GitHub/GitLab PR URL (if PR creation was configured) |
| `pr_number` | PR number (if PR creation was configured) |
| `error` | Set if the pipeline itself errored |

---

## Built-in validators

### `ShellCommandValidator`

Passes if the command exits with code 0.

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
    min_coverage=0.80,
    extra_args=["--strict-markers"],
    timeout_seconds=300,
)
```

### `CompositeValidator`

Runs multiple validators concurrently. AND (`"all"`) or OR (`"any"`) logic. Feedback from all validators is combined on retry.

```python
CompositeValidator([validator_a, validator_b], mode="all")
CompositeValidator([validator_a, validator_b], mode="any")
```

### `HttpHealthValidator`

Starts a process, polls an HTTP health endpoint, then tears it down.

```python
HttpHealthValidator(
    start_command="uvicorn app:main --port 8080",
    health_url="http://localhost:8080/health",
    expected_status=200,
    startup_timeout=30,
)
```

### Custom validator

Any object with a matching `validate()` signature works:

```python
class MyValidator:
    async def validate(self, repo_path: str, context) -> ValidationResult:
        return ValidationResult(
            passed=True,
            score=1.42,
            feedback="Sharpe 1.42, drawdown 12% — all thresholds met",
        )
```

---

## PR providers

### `GitHubPRProvider`

```python
from selfix.git import GitHubPRProvider

GitHubPRProvider(token=os.environ["GITHUB_TOKEN"])
```

Creates a PR via the GitHub REST API. Adds labels and requests reviewers automatically.

### `GitLabPRProvider`

```python
from selfix.git import GitLabPRProvider

GitLabPRProvider(
    token=os.environ["GITLAB_TOKEN"],
    base_url="https://gitlab.example.com",   # defaults to gitlab.com
)
```

Creates a merge request via the GitLab API.

---

## Webhook server

`SelfixWebhookServer` is a lightweight `aiohttp` HTTP server that receives external signals and dispatches pipeline runs.

| Route | Adapter |
|---|---|
| `POST /signal/error` | Raw `ErrorSignal` JSON |
| `POST /signal/metric` | Raw `MetricSignal` JSON |
| `POST /signal/manual` | Raw `ManualSignal` JSON |
| `POST /webhook/sentry` | Sentry issue webhook |
| `POST /webhook/datadog` | Datadog monitor alert |
| `POST /webhook/github` | GitHub Actions `workflow_run` failure |

Supports HMAC signature verification (`secret` parameter).

---

## Signal deduplication

`SignalRouter` deduplicates signals within a configurable window (default 5 minutes) using a SHA-256 fingerprint of the signal's meaningful content:

- `ErrorSignal` — fingerprinted by `error_type + file_hint + line_hint`
- `MetricSignal` — fingerprinted by `metric_name + metric_path`
- `ScheduledSignal` — fingerprinted by `cron + improvement_type + date` (fires at most once per day)
- `ManualSignal` — fingerprinted by `description`

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

Pipeline state is persisted to SQLite at `checkpoint_dir` (default `.selfix/checkpoints/selfix.db`). Each run is keyed by `signal.id`. If the process crashes mid-run and `selfix.run_sync` is called again with the same signal, LangGraph resumes from the last completed node automatically.

---

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| Phase 1 | Done | ManualSignal, ShellCommandValidator, single attempt |
| Phase 2 | Done | Retry loop, feedback injection, build gate, Pytest/Composite/HttpHealth validators, escalation, SQLite checkpointing |
| Phase 3 | **Done** | ErrorSignal, MetricSignal, ScheduledSignal, SignalRouter, remote repo cloning, GitHubPRProvider, GitLabPRProvider, SelfixWebhookServer |
| Phase 4 | Planned | Observability, LangSmith tracing, structured event log, CLI |
| Phase 5 | Planned | Multi-repo, parallel signal queuing |
