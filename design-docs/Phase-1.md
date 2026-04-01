# Selfix — Phase 1: Core Pipeline

> **Goal:** A working end-to-end autonomous fix pipeline triggered manually, validated by a caller-injected shell command, on a single local repository.  
> No signal routing, no retry loop, no PR creation yet. Just: *signal in → fix applied → validation passes → result reported.*

---

## 1. Phase 1 Scope

### In Scope
- `ManualSignal` — caller describes the problem or improvement in plain text
- `ShellCommandValidator` — caller provides a shell command; exit code 0 = pass
- Single local repository (no remote clone in Phase 1)
- Single fix attempt (no retry loop yet — that is Phase 2)
- LangGraph graph with full node structure (stubs where needed)
- Claude Agent SDK worker for exploration and fix generation
- Pipeline state persisted to disk via LangGraph checkpointing
- Structured result returned to caller (no PR, no GitHub yet)
- Basic console logging of agent reasoning and validation output

### Out of Scope (deferred)
- Retry loop and feedback injection → Phase 2
- Error/Metric/Cron signals → Phase 3
- PR creation → Phase 3
- LangSmith tracing → Phase 4
- Remote repo cloning → Phase 3
- Built-in language-specific validators (Pytest, etc.) → Phase 2

---

## 2. End-to-End Flow (Phase 1)

```
Caller
  │
  ├── constructs ManualSignal("The sorting algorithm in utils/sort.py is O(n²), improve it")
  ├── constructs ShellCommandValidator("pytest tests/ -x -q")
  ├── constructs SelfixConfig(repo_path, signal, validator)
  │
  └── await selfix.run(config)
            │
            ▼
     LangGraph Orchestrator
            │
            ├── [node] signal_intake      → parse signal, enrich state
            ├── [node] repo_setup         → verify repo path, create fix branch
            ├── [node] exploration        → Claude reads repo, produces summary
            ├── [node] fix_generation     → Claude edits files, produces diff
            ├── [node] build_check        → STUB (pass-through in Phase 1)
            ├── [node] validation         → run ShellCommandValidator
            ├── [node] retry_decision     → Phase 1: always proceed to result
            └── [node] report             → return SelfixResult to caller

            (pr_creation and escalation are stubs in Phase 1)
```

---

## 3. Public API (Phase 1 surface)

### 3.1 Entry point

```python
# selfix/__init__.py

async def run(config: SelfixConfig) -> SelfixResult:
    """
    Run the Selfix pipeline with the given config.
    Returns a SelfixResult describing what happened.
    """

def run_sync(config: SelfixConfig) -> SelfixResult:
    """
    Synchronous wrapper around run() for callers without an event loop.
    """
```

### 3.2 SelfixConfig

```python
# selfix/config.py

@dataclass
class SelfixConfig:
    repo_path: str                    # absolute path to local repo
    signal: Signal                    # what to fix / improve
    validator: SelfixValidator        # caller-injected validation logic
    max_attempts: int = 3             # retry limit (used from Phase 2)
    agent_config: AgentConfig = None  # optional model/tool overrides
    checkpoint_dir: str = ".selfix/checkpoints"

@dataclass
class AgentConfig:
    model: str = "claude-opus-4-6"
    max_tokens: int = 8192
    allowed_tools: list[str] = field(default_factory=lambda: [
        "Read", "Glob", "Grep", "Edit", "Bash"
    ])
    permission_mode: str = "bypassPermissions"  # safe for CI pipelines
```

### 3.3 SelfixResult

```python
# selfix/result.py

@dataclass
class SelfixResult:
    status: Literal["success", "failed", "escalated"]
    signal: Signal
    attempts: int
    diff: str | None               # unified diff of all changes made
    validation_result: ValidationResult | None
    agent_reasoning: str           # Claude's explanation of what it did
    branch_name: str | None        # git branch created
    error: str | None              # set if pipeline itself errored
```

---

## 4. Signal (Phase 1: ManualSignal only)

```python
# selfix/signals/base.py

@dataclass
class Signal:
    id: str                        # uuid, auto-generated
    created_at: datetime
    description: str               # human-readable summary
    scope_hint: str | None         # optional path hint, e.g. "src/utils/"

# selfix/signals/manual.py

@dataclass
class ManualSignal(Signal):
    """
    Triggered explicitly by the caller with a plain-text description
    of the problem or improvement to attempt.
    
    Example:
        ManualSignal(
            description="The Fibonacci function in math/fib.py uses recursion. 
                         Convert it to iteration and ensure it handles n > 10000.",
            scope_hint="math/"
        )
    """
    pass  # inherits all fields from Signal
```

---

## 5. Validator Protocol (Phase 1)

### 5.1 Protocol definition

```python
# selfix/validator/protocol.py

from typing import Protocol, runtime_checkable

@dataclass
class FixContext:
    signal: Signal
    repo_path: str
    diff: str                  # unified diff of changes applied
    attempt_number: int
    agent_reasoning: str       # Claude's explanation of the fix
    previous_feedback: str | None  # feedback from last ValidationResult (Phase 2+)

@dataclass
class ValidationResult:
    passed: bool
    score: float               # 0.0–1.0 or domain-specific (e.g. Sharpe ratio)
    feedback: str              # fed back to agent on retry — write actionably
    metadata: dict             # arbitrary structured data for observability

@runtime_checkable
class SelfixValidator(Protocol):
    async def validate(
        self,
        repo_path: str,
        context: FixContext,
    ) -> ValidationResult:
        """
        Validate whether the fix meets the caller's criteria.
        
        - repo_path: absolute path to the repo (with fix already applied)
        - context: full context about the fix attempt
        
        Returns ValidationResult. passed=True proceeds to PR/report.
        passed=False with rich feedback enables better retry attempts.
        """
        ...
```

### 5.2 ShellCommandValidator (Phase 1 built-in)

```python
# selfix/validator/builtin/shell.py

@dataclass
class ShellCommandValidator:
    """
    The simplest possible validator: run a shell command, 
    pass if exit code is 0.
    
    Examples:
        ShellCommandValidator("pytest tests/ -x -q")
        ShellCommandValidator("go test ./...")
        ShellCommandValidator("cargo test")
        ShellCommandValidator("npm test")
        ShellCommandValidator("python backtest.py --assert-sharpe 1.2")
    """
    command: str
    timeout_seconds: int = 300
    working_dir: str | None = None   # defaults to repo_path

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        cwd = self.working_dir or repo_path
        
        result = await asyncio.create_subprocess_shell(
            self.command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(
            result.communicate(), 
            timeout=self.timeout_seconds
        )
        
        output = stdout.decode()
        passed = result.returncode == 0
        
        return ValidationResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            feedback=output[-2000:],   # last 2000 chars — most relevant for retry
            metadata={
                "command": self.command,
                "exit_code": result.returncode,
                "full_output": output,
            }
        )
```

---

## 6. LangGraph Graph — Phase 1

### 6.1 PipelineState

```python
# selfix/graph/state.py

class PipelineState(TypedDict):
    # Inputs (set at pipeline start, never mutated)
    config: SelfixConfig
    signal: Signal
    
    # Repo
    repo_path: str
    branch_name: str | None
    
    # Agent outputs
    exploration_summary: str | None    # Claude's repo analysis
    fix_diff: str | None               # unified diff of applied changes
    agent_reasoning: str | None        # Claude's explanation
    
    # Validation
    validation_result: ValidationResult | None
    attempt_number: int                # starts at 1
    
    # Pipeline control
    status: Literal["running", "success", "failed", "escalated"]
    error: str | None
```

### 6.2 Graph definition

```python
# selfix/graph/orchestrator.py

def build_graph() -> CompiledGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("signal_intake",    signal_intake_node)
    builder.add_node("repo_setup",       repo_setup_node)
    builder.add_node("exploration",      exploration_node)
    builder.add_node("fix_generation",   fix_generation_node)
    builder.add_node("build_check",      build_check_node)
    builder.add_node("validation",       validation_node)
    builder.add_node("retry_decision",   retry_decision_node)
    builder.add_node("report",           report_node)
    builder.add_node("escalation",       escalation_node)    # stub Phase 1
    builder.add_node("pr_creation",      pr_creation_node)   # stub Phase 1

    builder.set_entry_point("signal_intake")

    builder.add_edge("signal_intake",   "repo_setup")
    builder.add_edge("repo_setup",      "exploration")
    builder.add_edge("exploration",     "fix_generation")
    builder.add_edge("fix_generation",  "build_check")
    builder.add_edge("build_check",     "validation")

    # Conditional routing after validation
    builder.add_conditional_edges(
        "retry_decision",
        route_after_retry,
        {
            "fix_generation": "fix_generation",   # retry (Phase 2+)
            "pr_creation":    "pr_creation",       # stub in Phase 1
            "escalation":     "escalation",        # stub in Phase 1
            "report":         "report",            # Phase 1 terminal
        }
    )

    builder.add_edge("validation",   "retry_decision")
    builder.add_edge("pr_creation",  "report")
    builder.add_edge("escalation",   "report")
    builder.set_finish_point("report")

    checkpointer = SqliteSaver.from_conn_string(":memory:")  # disk in Phase 2+
    return builder.compile(checkpointer=checkpointer)


def route_after_retry(state: PipelineState) -> str:
    """
    Phase 1: always go to report regardless of validation outcome.
    Phase 2 will implement proper retry and escalation logic.
    """
    return "report"
```

---

## 7. Node Definitions (Phase 1)

### 7.1 signal_intake_node

**Responsibility:** Parse the signal, assign a run ID, log the start.

```
Input:  config (SelfixConfig with Signal attached)
Output: state.signal enriched with run_id, state.attempt_number = 1
Side effects: console log "Selfix run started: <signal description>"
```

### 7.2 repo_setup_node

**Responsibility:** Verify the repo path exists, initialise git, create a fix branch.

```
Input:  state.repo_path, state.signal.id
Output: state.branch_name = "selfix/fix-<signal.id[:8]>"
Side effects: git checkout -b <branch_name>
Errors: raise if repo_path does not exist or is not a git repo
```

Branch naming convention: `selfix/fix-<signal-id[:8]>-<timestamp>`

### 7.3 exploration_node

**Responsibility:** Claude Agent SDK explores the repository and produces a structured summary. This summary is the agent's context for fix generation.

```
Input:  state.signal, state.repo_path
Output: state.exploration_summary (structured string)
```

Prompt structure passed to Claude Agent SDK:

```
You are exploring a code repository to understand a reported problem or 
improvement opportunity.

Signal: <signal.description>
Scope hint: <signal.scope_hint or "entire repository">
Repo path: <repo_path>

Tasks:
1. Understand the repository structure (languages, frameworks, entry points)
2. Locate the code most relevant to the signal
3. Understand the current implementation and why it is suboptimal
4. Identify all files that will need to change

Return a structured exploration summary:
- Relevant files (paths and purpose)
- Root cause or improvement opportunity
- Proposed approach for the fix
- Risks or considerations
```

Allowed tools: `Read`, `Glob`, `Grep` (no edits during exploration)

### 7.4 fix_generation_node

**Responsibility:** Claude Agent SDK applies the fix based on the exploration summary.

```
Input:  state.exploration_summary, state.signal, state.repo_path,
        state.previous_feedback (None in Phase 1)
Output: state.fix_diff, state.agent_reasoning
```

Prompt structure:

```
You are fixing a code repository based on your earlier exploration.

Signal: <signal.description>
Exploration summary: <exploration_summary>
Previous attempt feedback: <previous_feedback or "This is the first attempt">

Apply the fix now. Edit only the files identified in your exploration.
After editing, produce:
1. A brief explanation of exactly what you changed and why
2. Confirm the diff is complete

Be surgical. Do not rewrite files unnecessarily.
```

Allowed tools: `Read`, `Edit`, `Bash` (for running quick sanity checks only, not full test suite)

After the node completes, the orchestrator captures the git diff:

```python
# in fix_generation_node, after Claude agent completes:
diff = subprocess.check_output(
    ["git", "diff", "HEAD"],
    cwd=state["repo_path"]
).decode()
return {"fix_diff": diff, "agent_reasoning": agent_result.reasoning}
```

### 7.5 build_check_node (Phase 1: pass-through stub)

```python
async def build_check_node(state: PipelineState) -> dict:
    """
    Phase 1: stub. Always passes.
    Phase 2: run a fast compile/lint command before the expensive validator.
    """
    return {}
```

### 7.6 validation_node

**Responsibility:** Call the caller-injected validator, store the result.

```
Input:  state.config.validator, state.repo_path, full FixContext
Output: state.validation_result
```

```python
async def validation_node(state: PipelineState) -> dict:
    context = FixContext(
        signal=state["signal"],
        repo_path=state["repo_path"],
        diff=state["fix_diff"] or "",
        attempt_number=state["attempt_number"],
        agent_reasoning=state["agent_reasoning"] or "",
        previous_feedback=None,  # Phase 2+
    )
    result = await state["config"].validator.validate(
        state["repo_path"],
        context,
    )
    return {"validation_result": result}
```

### 7.7 retry_decision_node

**Responsibility:** Decide what happens next based on validation result.

Phase 1 behaviour: always routes to `report` (retry logic in Phase 2).

```python
async def retry_decision_node(state: PipelineState) -> dict:
    result = state["validation_result"]
    status = "success" if result.passed else "failed"
    return {"status": status}
```

### 7.8 report_node

**Responsibility:** Assemble and return the `SelfixResult` to the caller.

```python
async def report_node(state: PipelineState) -> dict:
    # Constructs SelfixResult from final state
    # Logs summary to console
    # Cleans up branch if validation failed (configurable)
    ...
```

---

## 8. Git Operations (Phase 1)

Minimal git wrapper needed for Phase 1:

```
selfix/git/repo.py

- verify_repo(path)          → raises if not a git repo
- create_branch(path, name)  → git checkout -b <name>
- get_diff(path)             → git diff HEAD (unified diff string)
- commit_changes(path, msg)  → git add -A && git commit -m <msg>
- delete_branch(path, name)  → git branch -D <name>  (on failure cleanup)
```

No remote push in Phase 1. Commit is local to the fix branch.

---

## 9. Caller Usage Examples (Phase 1)

### Python repo, pytest validation

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
        scope_hint="src/risk.py"
    ),
    validator=ShellCommandValidator("pytest tests/ -x -q"),
))

print(result.status)          # "success" or "failed"
print(result.diff)            # unified diff of changes
print(result.agent_reasoning) # Claude's explanation
```

### Go repo

```python
result = selfix.run_sync(SelfixConfig(
    repo_path="/home/user/projects/goservice",
    signal=ManualSignal(
        description="Fix the race condition reported in pkg/cache/lru.go"
    ),
    validator=ShellCommandValidator("go test ./... -race"),
))
```

### Algorithmic trading strategy (backtesting)

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

The backtest script is the caller's responsibility. Selfix just runs it and reads the exit code.

---

## 10. Phase 1 Deliverables

| # | Deliverable | Description |
|---|---|---|
| D1 | `selfix` Python package | Installable via `pip install selfix` or `uv add selfix` |
| D2 | `SelfixConfig`, `ManualSignal`, `ShellCommandValidator` | Core public API |
| D3 | `SelfixValidator` Protocol + `ValidationResult` + `FixContext` | Extension contract |
| D4 | LangGraph graph (all nodes, stubs where noted) | Full graph wired, Phase 1 logic |
| D5 | Claude Agent SDK worker (exploration + fix generation) | Working agent nodes |
| D6 | Git branch management | Create branch, capture diff, commit |
| D7 | `SelfixResult` returned to caller | Structured result with diff and reasoning |
| D8 | Console logging | Human-readable progress output |
| D9 | README with 3 usage examples | Python, Go, and backtest examples |
| D10 | Unit tests for validator protocol and graph routing | `pytest tests/` passes |

---

## 11. Phase 1 Success Criteria

The phase is complete when:

1. `selfix.run_sync(config)` runs end-to-end on a real local repository
2. Claude Agent SDK successfully reads and edits files in the repo
3. The caller's shell command validator runs and its result is captured
4. A `SelfixResult` with `diff`, `agent_reasoning`, and `validation_result` is returned
5. A `selfix/fix-*` git branch exists with the committed changes
6. All unit tests pass

---

## 12. What Phase 2 Adds

Phase 1 ends with a single pass — one attempt, no retry. Phase 2 introduces:

- **Retry loop** — `route_after_retry` routes back to `fix_generation` on failure
- **Feedback injection** — `ValidationResult.feedback` passed to next attempt prompt
- **Persistent checkpointing** — SQLite on disk, not in-memory
- **Built-in validators** — `PytestValidator`, `CompositeValidator`, `HttpHealthValidator`
- **Attempt tracking** — `attempt_number` increments, `max_attempts` enforced
- **Escalation node** — real implementation when max attempts reached

---

*Document version: 0.1 — Phase 1 design*  
*Status: Draft*  
*Depends on: High-Level-Design.md*
