# Selfix — Phase 2: Retry Loop & Feedback Injection

> **Goal:** Make the pipeline genuinely autonomous. A single pass is not enough for hard problems.  
> Phase 2 introduces the retry loop, feedback-driven fix improvement, persistent checkpointing,  
> built-in validators, and escalation when max attempts are exhausted.

---

## 1. Phase 2 Scope

### Builds On
Phase 1 delivered a working single-pass pipeline. Every node exists. The graph is wired.  
Phase 2 activates the conditional edges that Phase 1 stubbed out.

### In Scope
- Retry loop — `route_after_retry` routes back to `fix_generation` on failure
- Feedback injection — `ValidationResult.feedback` injected into the next attempt's prompt
- Persistent checkpointing — SQLite on disk, resumable across process restarts
- `attempt_number` tracking and `max_attempts` enforcement
- Escalation node — real implementation (notify caller, clean up branch)
- `build_check` node — real implementation (fast compile/lint gate)
- Built-in validators: `PytestValidator`, `CompositeValidator`, `HttpHealthValidator`
- Git: revert partial changes between attempts cleanly
- Structured attempt history in `PipelineState`

### Out of Scope (deferred)
- Error/Metric/Cron signals → Phase 3
- PR creation → Phase 3
- Remote repo cloning → Phase 3
- LangSmith tracing → Phase 4

---

## 2. The Retry Loop

The central addition in Phase 2. After validation fails, the pipeline does not terminate — it loops back to `fix_generation` with the validator's feedback injected into the agent's prompt.

```
exploration
     │
     ▼
fix_generation ◀─────────────────────────────────────┐
     │                                               │
     ▼                                               │
build_check                                          │
     │  (fail fast on syntax errors)                 │
     ▼                                               │
validation                                           │
     │                                               │
     ▼                                               │
retry_decision                                       │
     ├── passed ──────────────────────────► pr_creation (Phase 3 stub)
     ├── failed + attempts < max ──────────────────── ┘  (retry)
     └── failed + attempts >= max ─────────► escalation
```

Key design constraint: **exploration runs only once**. The agent's understanding of the repo is reused across all attempts. Only `fix_generation` repeats, with progressively richer context.

---

## 3. PipelineState Changes

New fields added to `PipelineState`:

```python
class PipelineState(TypedDict):
    # --- existing Phase 1 fields ---
    config: SelfixConfig
    signal: Signal
    repo_path: str
    branch_name: str | None
    exploration_summary: str | None
    fix_diff: str | None
    agent_reasoning: str | None
    validation_result: ValidationResult | None
    attempt_number: int
    status: Literal["running", "success", "failed", "escalated"]
    error: str | None

    # --- new Phase 2 fields ---
    attempt_history: list[AttemptRecord]   # full record of every attempt
    current_feedback: str | None           # feedback from last ValidationResult
    build_check_output: str | None         # output from fast compile/lint gate
    base_commit: str                       # git SHA before any edits (for clean revert)
```

### AttemptRecord

Captures everything about one fix attempt for observability and retry context:

```python
@dataclass
class AttemptRecord:
    attempt_number: int
    diff: str
    agent_reasoning: str
    build_passed: bool
    validation_result: ValidationResult
    started_at: datetime
    completed_at: datetime
```

The full `attempt_history` list is available to the agent on each retry, enabling it to reason about what it already tried and why it did not work.

---

## 4. Retry Logic — retry_decision_node

```python
async def retry_decision_node(state: PipelineState) -> dict:
    result = state["validation_result"]
    attempt = state["attempt_number"]
    max_attempts = state["config"].max_attempts

    # Record this attempt
    record = AttemptRecord(
        attempt_number=attempt,
        diff=state["fix_diff"] or "",
        agent_reasoning=state["agent_reasoning"] or "",
        build_passed=state["build_check_output"] is not None,
        validation_result=result,
        started_at=...,
        completed_at=datetime.utcnow(),
    )
    history = state["attempt_history"] + [record]

    if result.passed:
        return {
            "status": "success",
            "attempt_history": history,
        }

    if attempt >= max_attempts:
        return {
            "status": "escalated",
            "attempt_history": history,
            "current_feedback": result.feedback,
        }

    # Revert repo to base commit before retrying
    await revert_to_base(state["repo_path"], state["base_commit"])

    return {
        "attempt_number": attempt + 1,
        "attempt_history": history,
        "current_feedback": result.feedback,
        "fix_diff": None,
        "agent_reasoning": None,
        "validation_result": None,
        "build_check_output": None,
    }


def route_after_retry(state: PipelineState) -> str:
    return {
        "success":   "pr_creation",
        "escalated": "escalation",
        "running":   "fix_generation",
    }[state["status"]]
```

---

## 5. Feedback Injection — fix_generation_node (updated)

The key change from Phase 1: the agent now receives the full attempt history and the latest feedback when retrying.

```
You are fixing a code repository. This is attempt <N> of <max>.

Signal: <signal.description>
Exploration summary: <exploration_summary>

--- Attempt History ---
<for each previous attempt>
Attempt <N>:
  What was changed: <agent_reasoning>
  Diff applied:
    <diff>
  Validation feedback:
    <validation_result.feedback>
</for>

--- Your Task ---
The previous attempt(s) did not pass validation.
Study the feedback carefully. Do not repeat the same approach.

Key guidance from last validation:
<current_feedback>

Apply a revised fix now.
```

This prompt structure means the agent reasons about *why* each previous attempt failed before generating the next fix. The richer the validator's `feedback`, the more targeted the retry.

---

## 6. Git Revert Between Attempts

Between retry attempts, all file changes from the previous attempt must be cleanly reverted before the agent starts fresh.

```python
# selfix/git/repo.py

async def revert_to_base(repo_path: str, base_commit: str) -> None:
    """
    Revert all changes back to base_commit.
    Preserves the branch — only the working tree and index are reset.
    """
    subprocess.run(
        ["git", "reset", "--hard", base_commit],
        cwd=repo_path,
        check=True,
    )

async def capture_base_commit(repo_path: str) -> str:
    """
    Returns the current HEAD SHA before any edits.
    Called in repo_setup_node and stored in state.base_commit.
    """
    result = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
    )
    return result.decode().strip()
```

After revert, the repo is in its original state on the fix branch. The next `fix_generation` run starts from a clean slate, but with full knowledge of what was tried.

---

## 7. build_check_node — Real Implementation

Phase 1 was a stub. Phase 2 makes it a fast gate that catches obvious failures (syntax errors, compile failures) before running the potentially expensive validator.

```python
# selfix/graph/nodes/build_check.py

async def build_check_node(state: PipelineState) -> dict:
    build_cmd = state["config"].build_command
    if not build_cmd:
        # No build command configured — skip
        return {"build_check_output": "skipped"}

    result = await run_shell(
        build_cmd,
        cwd=state["repo_path"],
        timeout=60,
    )

    if result.returncode != 0:
        # Treat build failure as a failed validation with feedback
        return {
            "validation_result": ValidationResult(
                passed=False,
                score=0.0,
                feedback=f"Build failed before validation could run:\n{result.output[-1000:]}",
                metadata={"build_command": build_cmd, "exit_code": result.returncode},
            ),
            "build_check_output": result.output,
        }

    return {"build_check_output": result.output}
```

`SelfixConfig` gains an optional `build_command` field:

```python
@dataclass
class SelfixConfig:
    ...
    build_command: str | None = None   # e.g. "dotnet build", "tsc --noEmit", "cargo check"
```

The graph routing skips `validation` if `build_check` already populated `validation_result`:

```python
def route_after_build_check(state: PipelineState) -> str:
    if state.get("validation_result") is not None:
        # build failed — go straight to retry_decision
        return "retry_decision"
    return "validation"
```

---

## 8. Persistent Checkpointing

Phase 1 used an in-memory checkpointer (lost on crash). Phase 2 persists to SQLite.

```python
# selfix/graph/orchestrator.py

def build_graph(checkpoint_dir: str) -> CompiledGraph:
    os.makedirs(checkpoint_dir, exist_ok=True)
    db_path = os.path.join(checkpoint_dir, "selfix.db")

    checkpointer = SqliteSaver.from_conn_string(db_path)
    return builder.compile(checkpointer=checkpointer)
```

Each pipeline run uses the `signal.id` as the LangGraph thread ID:

```python
config = {"configurable": {"thread_id": signal.id}}
await graph.ainvoke(initial_state, config=config)
```

If the process crashes mid-run and `selfix.run()` is called again with the same signal, LangGraph resumes from the last completed node automatically. No work is repeated.

Resume behaviour:
- If crashed during `fix_generation` → restart from `fix_generation`
- If crashed during `validation` → restart from `validation` (validator reruns)
- If crashed during `retry_decision` → restart from `retry_decision`

---

## 9. Escalation Node — Real Implementation

Phase 1 stubbed escalation. Phase 2 implements it.

```python
# selfix/graph/nodes/escalation.py

async def escalation_node(state: PipelineState) -> dict:
    """
    Called when max_attempts reached without passing validation.
    
    Responsibilities:
    1. Emit an EscalationEvent to the caller's event handler (if configured)
    2. Leave the fix branch intact for manual inspection
    3. Write an escalation report summarising all attempts
    4. Set status = "escalated"
    """
    report = build_escalation_report(state)

    # Write report to branch
    report_path = os.path.join(state["repo_path"], ".selfix", "escalation-report.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)

    await git_commit(
        state["repo_path"],
        ".selfix/escalation-report.md",
        f"selfix: escalation report after {state['attempt_number']} attempts",
    )

    # Notify caller via event handler if configured
    if handler := state["config"].escalation_handler:
        await handler(EscalationEvent(
            signal=state["signal"],
            attempts=state["attempt_history"],
            branch_name=state["branch_name"],
        ))

    return {"status": "escalated"}
```

`SelfixConfig` gains an optional `escalation_handler`:

```python
@dataclass
class SelfixConfig:
    ...
    escalation_handler: Callable[[EscalationEvent], Awaitable[None]] | None = None
```

Escalation report format (written to `.selfix/escalation-report.md` on the branch):

```markdown
# Selfix Escalation Report

Signal: <description>
Attempts: <N> / <max>
Branch: selfix/fix-<id>

## Attempt 1
**What was tried:** <agent_reasoning>
**Validation feedback:** <feedback>

## Attempt 2
...

## Recommendation
Manual intervention required. Review the branch and validation feedback above.
```

---

## 10. Built-in Validators (Phase 2)

### 10.1 PytestValidator

```python
@dataclass
class PytestValidator:
    """
    Runs pytest and passes if all tests pass.
    Optionally enforces a minimum coverage threshold.
    """
    test_path: str = "tests/"
    min_coverage: float | None = None    # e.g. 0.80 for 80%
    extra_args: list[str] = field(default_factory=list)
    timeout_seconds: int = 300

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        args = ["pytest", self.test_path, "-x", "-q", "--tb=short"]
        if self.min_coverage:
            args += [f"--cov={repo_path}", f"--cov-fail-under={int(self.min_coverage * 100)}"]
        args += self.extra_args

        result = await run_shell(" ".join(args), cwd=repo_path, timeout=self.timeout_seconds)
        passed = result.returncode == 0

        return ValidationResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            feedback=result.output[-2000:],
            metadata={"command": " ".join(args), "exit_code": result.returncode},
        )
```

### 10.2 CompositeValidator

```python
@dataclass
class CompositeValidator:
    """
    Runs multiple validators. Passes only if ALL pass (AND logic).
    Feedback combines all failures for richer retry context.
    """
    validators: list[SelfixValidator]
    mode: Literal["all", "any"] = "all"   # "any" = OR logic

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        results = await asyncio.gather(*[
            v.validate(repo_path, context) for v in self.validators
        ])

        if self.mode == "all":
            passed = all(r.passed for r in results)
        else:
            passed = any(r.passed for r in results)

        score = sum(r.score for r in results) / len(results)
        feedback = "\n\n".join([
            f"Validator {i+1}: {'PASSED' if r.passed else 'FAILED'}\n{r.feedback}"
            for i, r in enumerate(results)
        ])

        return ValidationResult(
            passed=passed,
            score=score,
            feedback=feedback,
            metadata={"individual_results": [r.metadata for r in results]},
        )
```

### 10.3 HttpHealthValidator

```python
@dataclass
class HttpHealthValidator:
    """
    Starts a process, waits for it to be healthy, validates via HTTP,
    then tears it down. Useful for service-level validation.
    """
    start_command: str               # e.g. "uvicorn app:main --port 8080"
    health_url: str                  # e.g. "http://localhost:8080/health"
    expected_status: int = 200
    startup_timeout: int = 30
    request_timeout: int = 10

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        # Start the service
        proc = await asyncio.create_subprocess_shell(
            self.start_command, cwd=repo_path,
        )
        try:
            # Wait for health endpoint to respond
            await self._wait_for_health()
            # Run the actual check
            passed, feedback = await self._check_health()
        finally:
            proc.terminate()

        return ValidationResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            feedback=feedback,
            metadata={"url": self.health_url, "start_command": self.start_command},
        )
```

---

## 11. Updated Caller Usage Examples

### With retry and composite validation

```python
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

### Algo backtest with 3 retry attempts

```python
result = selfix.run_sync(SelfixConfig(
    repo_path="/projects/algo",
    signal=ManualSignal(
        description="""
            Sharpe ratio is 0.8 on SPY 2020-2024 backtest.
            Improve to > 1.2 without exceeding 15% max drawdown.
        """
    ),
    validator=ShellCommandValidator(
        "python backtest.py --assert-min-sharpe 1.2 --assert-max-drawdown 0.15",
        timeout_seconds=600,
    ),
    max_attempts=3,
))

# After 3 attempts, check what happened
for i, attempt in enumerate(result.attempt_history):
    print(f"Attempt {i+1}: passed={attempt.validation_result.passed}")
    print(f"  Feedback: {attempt.validation_result.feedback}")
```

---

## 12. Phase 2 Deliverables

| # | Deliverable | Description |
|---|---|---|
| D1 | Retry loop | Conditional edge routes back to `fix_generation` on failure |
| D2 | Feedback injection | `current_feedback` and `attempt_history` injected into fix prompt |
| D3 | Git revert between attempts | `revert_to_base()` restores clean working tree per attempt |
| D4 | Persistent checkpointing | SQLite on disk, resumable across crashes |
| D5 | `build_check_node` real implementation | Fast compile/lint gate with routing |
| D6 | `escalation_node` real implementation | Report written, handler called, branch preserved |
| D7 | `PytestValidator` | Built-in with optional coverage enforcement |
| D8 | `CompositeValidator` | AND/OR logic across multiple validators |
| D9 | `HttpHealthValidator` | Service start → health check → teardown |
| D10 | `AttemptRecord` + attempt history in state | Full audit trail of all attempts |
| D11 | Updated unit + integration tests | Retry paths, escalation, all new validators |

---

## 13. Phase 2 Success Criteria

1. A pipeline that fails validation on attempt 1 retries with feedback injected into the prompt
2. A pipeline that fails all `max_attempts` escalates correctly and writes an escalation report
3. The pipeline resumes correctly after a simulated crash at each node
4. `CompositeValidator` with two validators only passes when both pass
5. `build_check` failure correctly skips `validation` and goes to `retry_decision`
6. All Phase 1 success criteria still pass

---

## 14. What Phase 3 Added

Phase 2 ends with a fully autonomous retry loop on a local repo. Phase 3 (complete) delivered:

- **Signal router** — `ErrorSignal`, `MetricSignal`, `ScheduledSignal` with deduplication via `SignalRouter`
- **Remote repo support** — `RepoConfig` + `RepoManager` for clone, fetch-latest, and branch push
- **PR creation node** — real implementation: branch push + `GitHubPRProvider` / `GitLabPRProvider`
- **Webhook listener** — `SelfixWebhookServer` with Sentry, Datadog, and GitHub Actions adapters
- **Signal enrichment** — `agent_focus_hint` injected into exploration prompt per signal type

---

*Document version: 0.1 — Phase 2 design*  
*Status: Draft*  
*Depends on: Phase-1.md*
