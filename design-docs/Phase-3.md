# Selfix — Phase 3: Signal Router, Remote Repos & PR Creation

> **Goal:** Take the pipeline from local tool to production autonomous system.  
> Phase 3 connects Selfix to the real world: external signals trigger runs automatically,  
> repos are cloned from remote, and passing fixes are opened as pull requests.

---

## 1. Phase 3 Scope

### Builds On
Phase 2 delivered a fully autonomous retry loop on local repos with persistent state.  
Phase 3 makes Selfix react to the world and write back to it.

### In Scope
- Signal router — receives, routes, and queues incoming signals
- `ErrorSignal` — triggered by exception traces, log lines, Sentry webhooks
- `MetricSignal` — triggered by threshold breaches (latency, error rate, coverage drop)
- `ScheduledSignal` — cron-driven scheduled improvement scans
- Remote repo support — clone from GitHub/GitLab, push fix branch
- PR creation node — real implementation with structured PR body
- Webhook listener — HTTP server that receives signals from external systems
- GitHub / GitLab PR adapter — abstracted behind a `PRProvider` protocol
- Signal deduplication — avoid running duplicate fix attempts for the same problem

### Out of Scope (deferred)
- LangSmith tracing → Phase 4
- CLI → Phase 4
- Multi-repo parallel execution → Phase 5

---

## 2. Signal Types (Phase 3 additions)

### 2.1 ErrorSignal

Triggered when an exception or error is detected in logs, monitoring, or CI.

```python
@dataclass
class ErrorSignal(Signal):
    """
    Triggered by a detected error: exception, stack trace, or log pattern.

    Example — from Sentry webhook:
        ErrorSignal(
            description="NullPointerException in UserService.getProfile()",
            stack_trace="...",
            file_hint="src/services/UserService.java",
            line_hint=142,
            error_type="NullPointerException",
            frequency=47,          # occurrences in last hour
            first_seen=datetime,
            environment="production",
        )
    """
    stack_trace: str | None = None
    file_hint: str | None = None
    line_hint: int | None = None
    error_type: str | None = None
    frequency: int | None = None
    environment: str | None = None
```

### 2.2 MetricSignal

Triggered when a quantitative metric crosses a threshold.

```python
@dataclass
class MetricSignal(Signal):
    """
    Triggered by a metric regression.

    Example — latency regression:
        MetricSignal(
            description="p99 latency on /api/search regressed from 80ms to 340ms",
            metric_name="http.request.duration.p99",
            metric_path="/api/search",
            current_value=340.0,
            baseline_value=80.0,
            threshold=150.0,
            unit="ms",
            direction="lower_is_better",
        )

    Example — coverage drop:
        MetricSignal(
            description="Test coverage dropped from 84% to 71%",
            metric_name="test.coverage",
            current_value=71.0,
            baseline_value=84.0,
            threshold=80.0,
            unit="percent",
            direction="higher_is_better",
        )
    """
    metric_name: str = ""
    metric_path: str | None = None
    current_value: float = 0.0
    baseline_value: float | None = None
    threshold: float | None = None
    unit: str = ""
    direction: Literal["higher_is_better", "lower_is_better"] = "lower_is_better"
```

### 2.3 ScheduledSignal

Triggered by a cron expression for proactive improvement scans.

```python
@dataclass
class ScheduledSignal(Signal):
    """
    Triggered on a schedule for proactive improvement scans.

    Example — nightly security scan:
        ScheduledSignal(
            description="Nightly security hardening scan",
            cron="0 2 * * *",
            improvement_type="security",
            scope_hint="src/",
        )

    Example — weekly performance scan:
        ScheduledSignal(
            description="Weekly algorithm performance improvement pass",
            cron="0 9 * * 1",
            improvement_type="performance",
        )
    """
    cron: str = ""
    improvement_type: Literal[
        "security", "performance", "maintainability", "coverage", "general"
    ] = "general"
```

---

## 3. Signal Router

The signal router is the entry point for all external triggers. It receives signals, deduplicates them, and dispatches pipeline runs.

```
External Source                Signal Router              Pipeline
─────────────────              ─────────────              ────────
Sentry webhook    ──────────►  ErrorSignal    ──────────► selfix.run()
Datadog alert     ──────────►  MetricSignal   ──────────► selfix.run()
Cron scheduler    ──────────►  ScheduledSignal ─────────► selfix.run()
Manual call       ──────────►  ManualSignal   ──────────► selfix.run()
```

### 3.1 SignalRouter

```python
# selfix/signals/router.py

class SignalRouter:
    """
    Receives signals from any source, deduplicates, and dispatches pipeline runs.
    Can be used standalone or via the webhook listener.
    """

    def __init__(self, config_factory: Callable[[Signal], SelfixConfig]):
        self.config_factory = config_factory
        self._seen: dict[str, datetime] = {}   # signal fingerprint → last seen
        self._dedup_window_seconds = 300       # 5 min dedup window

    async def dispatch(self, signal: Signal) -> SelfixResult | None:
        fingerprint = self._fingerprint(signal)

        if self._is_duplicate(fingerprint):
            return None  # silently deduplicate

        self._seen[fingerprint] = datetime.utcnow()
        config = self.config_factory(signal)
        return await selfix.run(config)

    def _fingerprint(self, signal: Signal) -> str:
        """
        Stable hash of the signal's meaningful content.
        ErrorSignal: hash of error_type + file_hint + line_hint
        MetricSignal: hash of metric_name + metric_path
        ScheduledSignal: hash of cron + improvement_type + date
        ManualSignal: hash of description
        """
        ...

    def _is_duplicate(self, fingerprint: str) -> bool:
        last = self._seen.get(fingerprint)
        if not last:
            return False
        return (datetime.utcnow() - last).seconds < self._dedup_window_seconds
```

### 3.2 Signal enrichment in signal_intake_node

Each signal type enriches `PipelineState` differently, giving the agent more focused context:

```python
async def signal_intake_node(state: PipelineState) -> dict:
    signal = state["signal"]

    enrichment = {}

    if isinstance(signal, ErrorSignal):
        enrichment["agent_focus_hint"] = (
            f"Focus on {signal.file_hint} line {signal.line_hint}. "
            f"Error type: {signal.error_type}. "
            f"Stack trace provided."
        )

    elif isinstance(signal, MetricSignal):
        enrichment["agent_focus_hint"] = (
            f"Metric '{signal.metric_name}' regressed from "
            f"{signal.baseline_value}{signal.unit} to "
            f"{signal.current_value}{signal.unit}. "
            f"Target: {signal.direction.replace('_', ' ')} than {signal.threshold}{signal.unit}."
        )

    elif isinstance(signal, ScheduledSignal):
        enrichment["agent_focus_hint"] = (
            f"Proactive {signal.improvement_type} scan across {signal.scope_hint or 'entire repo'}."
        )

    return {**enrichment, "attempt_number": 1, "attempt_history": []}
```

---

## 4. Remote Repo Support

### 4.1 RepoManager

Phase 1-2 assumed the repo was already local. Phase 3 adds full remote repo lifecycle management.

```python
# selfix/git/repo.py

@dataclass
class RepoConfig:
    url: str                          # https or ssh git URL
    local_path: str                   # where to clone / find it
    auth_token: str | None = None     # GitHub/GitLab PAT
    clone_depth: int | None = 50      # shallow clone for speed; None = full

class RepoManager:

    async def ensure_local(self, config: RepoConfig) -> str:
        """
        If repo already exists locally, fetch latest main.
        If not, clone it.
        Returns absolute path to local repo.
        """
        if os.path.exists(os.path.join(config.local_path, ".git")):
            await self._fetch_latest(config)
        else:
            await self._clone(config)
        return config.local_path

    async def _clone(self, config: RepoConfig) -> None:
        url = self._inject_token(config.url, config.auth_token)
        args = ["git", "clone", url, config.local_path]
        if config.clone_depth:
            args += ["--depth", str(config.clone_depth)]
        subprocess.run(args, check=True)

    async def push_branch(self, repo_path: str, branch_name: str) -> None:
        subprocess.run(
            ["git", "push", "origin", branch_name, "--set-upstream"],
            cwd=repo_path,
            check=True,
        )

    def _inject_token(self, url: str, token: str | None) -> str:
        if not token:
            return url
        # https://token@github.com/org/repo.git
        return url.replace("https://", f"https://{token}@")
```

`SelfixConfig` gains a `repo_config` field for remote repos:

```python
@dataclass
class SelfixConfig:
    repo_path: str | None = None           # local path (Phase 1/2 style)
    repo_config: RepoConfig | None = None  # remote repo (Phase 3)
    # At least one of repo_path or repo_config must be set
```

---

## 5. PR Creation Node — Real Implementation

Phase 1 and 2 stubbed `pr_creation`. Phase 3 implements it.

### 5.1 PRProvider Protocol

Abstracted so the same code works with GitHub and GitLab.

```python
# selfix/git/pr.py

class PRProvider(Protocol):
    async def create_pull_request(self, request: PRRequest) -> PRResult:
        ...

@dataclass
class PRRequest:
    repo_url: str
    base_branch: str          # usually "main" or "master"
    head_branch: str          # the selfix/fix-* branch
    title: str
    body: str
    labels: list[str]
    reviewers: list[str]
    draft: bool

@dataclass
class PRResult:
    pr_url: str
    pr_number: int
    created_at: datetime
```

### 5.2 PR body structure

The PR body is generated by Selfix automatically from pipeline state:

```markdown
## Selfix Autonomous Fix

**Signal:** <signal.description>
**Triggered by:** <signal type> at <created_at>
**Attempts:** <N>
**Validation:** ✅ PASSED (score: <score>)

---

## What Changed

<agent_reasoning from final successful attempt>

---

## Diff Summary

<auto-generated: N files changed, X insertions, Y deletions>

---

## Validation Report

**Command / Validator:** <validator description>
**Score:** <score>
**Output:**
```
<validation output, truncated to 1000 chars>
```

---

## Attempt History

<if attempts > 1>
| Attempt | Result | Feedback Summary |
|---------|--------|-----------------|
| 1 | ❌ Failed | <first 100 chars of feedback> |
| 2 | ✅ Passed | — |
</if>

---
*Opened automatically by [Selfix](https://github.com/your-org/selfix)*
```

### 5.3 pr_creation_node

```python
async def pr_creation_node(state: PipelineState) -> dict:
    config = state["config"]

    # Push the branch to remote
    await repo_manager.push_branch(state["repo_path"], state["branch_name"])

    # Build PR body
    body = build_pr_body(state)
    title = build_pr_title(state["signal"])

    request = PRRequest(
        repo_url=config.repo_config.url,
        base_branch=config.pr_config.base_branch or "main",
        head_branch=state["branch_name"],
        title=title,
        body=body,
        labels=config.pr_config.labels or ["selfix", "automated"],
        reviewers=config.pr_config.reviewers or [],
        draft=config.pr_config.draft or False,
    )

    result = await config.pr_provider.create_pull_request(request)

    return {
        "pr_url": result.pr_url,
        "pr_number": result.pr_number,
        "status": "success",
    }
```

### 5.4 PRConfig

```python
@dataclass
class PRConfig:
    base_branch: str = "main"
    labels: list[str] = field(default_factory=lambda: ["selfix", "automated"])
    reviewers: list[str] = field(default_factory=list)
    draft: bool = False               # open as draft PR for review before merge
    auto_merge: bool = False          # enable GitHub auto-merge if checks pass
```

### 5.5 GitHubPRProvider

```python
# selfix/git/providers/github.py

class GitHubPRProvider:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"

    async def create_pull_request(self, request: PRRequest) -> PRResult:
        owner, repo = self._parse_repo_url(request.repo_url)

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{self.base_url}/repos/{owner}/{repo}/pulls",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "title": request.title,
                    "body": request.body,
                    "head": request.head_branch,
                    "base": request.base_branch,
                    "draft": request.draft,
                },
            )
            data = await resp.json()

        # Add labels
        if request.labels:
            await self._add_labels(owner, repo, data["number"], request.labels)

        # Request reviewers
        if request.reviewers:
            await self._request_reviewers(owner, repo, data["number"], request.reviewers)

        return PRResult(
            pr_url=data["html_url"],
            pr_number=data["number"],
            created_at=datetime.utcnow(),
        )
```

---

## 6. Webhook Listener

A lightweight HTTP server that receives signals from external systems and dispatches pipeline runs.

```python
# selfix/signals/webhook.py

from aiohttp import web

class SelfixWebhookServer:
    """
    HTTP server that receives signals from monitoring, CI, or alerting systems
    and dispatches Selfix pipeline runs.

    Supports:
    - POST /signal/error     → ErrorSignal
    - POST /signal/metric    → MetricSignal
    - POST /webhook/sentry   → Sentry issue webhook → ErrorSignal
    - POST /webhook/datadog  → Datadog monitor alert → MetricSignal
    - POST /webhook/github   → GitHub Actions failure → ErrorSignal
    """

    def __init__(self, router: SignalRouter, secret: str | None = None):
        self.router = router
        self.secret = secret
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_post("/signal/error",      self.handle_error_signal)
        self.app.router.add_post("/signal/metric",     self.handle_metric_signal)
        self.app.router.add_post("/signal/manual",     self.handle_manual_signal)
        self.app.router.add_post("/webhook/sentry",    self.handle_sentry)
        self.app.router.add_post("/webhook/datadog",   self.handle_datadog)
        self.app.router.add_post("/webhook/github",    self.handle_github_actions)

    async def handle_sentry(self, request: web.Request) -> web.Response:
        payload = await request.json()
        signal = ErrorSignal(
            description=payload["data"]["issue"]["title"],
            stack_trace=payload["data"]["issue"].get("culprit"),
            error_type=payload["data"]["issue"]["type"],
            frequency=payload["data"]["issue"].get("times_seen"),
            environment=payload["data"]["issue"].get("environment"),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    async def run(self, host: str = "0.0.0.0", port: int = 8765):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
```

---

## 7. Updated SelfixConfig (Phase 3 complete)

```python
@dataclass
class SelfixConfig:
    # Repo
    repo_path: str | None = None          # local path
    repo_config: RepoConfig | None = None # remote repo

    # Core
    signal: Signal = None
    validator: SelfixValidator = None
    max_attempts: int = 3
    build_command: str | None = None

    # Agent
    agent_config: AgentConfig = None

    # PR
    pr_config: PRConfig = field(default_factory=PRConfig)
    pr_provider: PRProvider | None = None  # required for PR creation

    # Handlers
    escalation_handler: Callable[[EscalationEvent], Awaitable[None]] | None = None

    # Persistence
    checkpoint_dir: str = ".selfix/checkpoints"
```

---

## 8. Updated SelfixResult (Phase 3)

```python
@dataclass
class SelfixResult:
    status: Literal["success", "failed", "escalated"]
    signal: Signal
    attempts: int
    attempt_history: list[AttemptRecord]
    diff: str | None
    validation_result: ValidationResult | None
    agent_reasoning: str | None
    branch_name: str | None
    pr_url: str | None                  # new in Phase 3
    pr_number: int | None               # new in Phase 3
    error: str | None
```

---

## 9. Caller Usage Examples (Phase 3)

### Full production setup — remote GitHub repo

```python
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
    escalation_handler=lambda event: post_to_slack(
        f"Selfix could not fix: {event.signal.description}. "
        f"Branch {event.branch_name} left for manual review."
    ),
))

print(f"PR opened: {result.pr_url}")
```

### Webhook-driven setup

```python
from selfix.signals import SelfixWebhookServer, SignalRouter

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
# Now configure Sentry / Datadog to POST to http://your-host:8765/webhook/sentry
```

---

## 10. Phase 3 Deliverables

| # | Deliverable | Description |
|---|---|---|
| D1 | `ErrorSignal` | Stack trace, file hint, error type, frequency |
| D2 | `MetricSignal` | Metric name, current/baseline/threshold values |
| D3 | `ScheduledSignal` | Cron expression, improvement type |
| D4 | `SignalRouter` | Deduplication + dispatch to pipeline |
| D5 | `RepoManager` | Clone, fetch latest, push branch |
| D6 | `pr_creation_node` real implementation | Generates PR body, opens PR |
| D7 | `GitHubPRProvider` | GitHub REST API adapter |
| D8 | `GitLabPRProvider` | GitLab REST API adapter |
| D9 | `SelfixWebhookServer` | HTTP listener for Sentry, Datadog, GitHub Actions |
| D10 | `PRConfig` + `RepoConfig` | Full config for remote repos and PR settings |
| D11 | Updated `SelfixResult` with `pr_url`, `pr_number` | |
| D12 | Integration tests against a local Gitea instance | End-to-end PR creation without GitHub rate limits |

---

## 11. Phase 3 Success Criteria

1. An `ErrorSignal` received via webhook triggers a full pipeline run and opens a real PR
2. A `MetricSignal` correctly enriches the agent's exploration prompt with metric context
3. Signal deduplication prevents two runs for the same error within the dedup window
4. PR body contains agent reasoning, diff summary, and validation report
5. `GitLabPRProvider` opens a merge request on a local GitLab instance
6. A `ScheduledSignal` triggered by cron runs a proactive improvement scan end-to-end
7. All Phase 1 and Phase 2 success criteria still pass

---

## 12. What Phase 4 Adds

Phase 3 ends with a production-capable autonomous pipeline. Phase 4 introduces:

- **LangSmith tracing** — every node, tool call, and agent decision traced and queryable
- **Structured event log** — machine-readable event stream for external observability tools
- **CLI** — `selfix run`, `selfix watch`, `selfix status`, `selfix history`
- **Packaging** — `pyproject.toml`, published to PyPI

---

*Document version: 0.2 — Phase 3 design*  
*Status: Implemented*  
*Depends on: Phase-2.md*
