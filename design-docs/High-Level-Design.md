# Selfix — High-Level Design

> **Selfix** is a language-agnostic, autonomous code improvement pipeline.  
> It watches for signals, lets an AI agent explore and fix a repository, validates the result against caller-injected criteria, and opens a pull request — entirely without human intervention.

---

## 1. Vision

Most CI pipelines tell you when something is broken. Selfix **fixes it**.

The core insight is that "correctness" is not Selfix's problem to define — it is the caller's. Selfix is the orchestration engine. The caller owns what "better" means: passing tests, improved benchmark scores, higher backtesting Sharpe ratios, lower latency p99, or any quantifiable criterion. As long as the caller can express a pass/fail signal and a feedback string, Selfix can improve toward it.

---

## 2. Goals

| # | Goal |
|---|---|
| G1 | Language-agnostic — works on any repo: Python, TypeScript, Go, Rust, .NET, etc. |
| G2 | Validation-agnostic — caller injects any validation logic via a protocol |
| G3 | Signal-agnostic — triggered by errors, metric regressions, schedules, or manual invocation |
| G4 | Fully autonomous — no human intervention required in the happy path |
| G5 | Resumable — a crashed run picks up from its last checkpoint |
| G6 | Observable — every agent decision, tool call, and validation result is traceable |
| G7 | Packageable — distributed as a pip-installable Python package |
| G8 | Safe — destructive actions (file edits, PR creation) are scoped and auditable |

---

## 3. Non-Goals (v1)

- Selfix does **not** provision infrastructure or deploy artefacts
- Selfix does **not** manage secrets beyond what the caller passes in
- Selfix does **not** support parallel simultaneous fix attempts on the same repo (sequential only in v1)
- Selfix does **not** provide a UI — it is a library / CLI, not a SaaS product

---

## 4. System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         CALLER / HOST                            │
│                                                                  │
│   signal_source = CronSignal("0 2 * * *")                        │
│   validator     = BacktestValidator(dataset, min_sharpe=1.2)     │
│   config        = SelfixConfig(repo=..., signal=..., validator=) │
│                                                                  │
│   await selfix.run(config)                                       │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                    SELFIX CORE (this package)                    │
│                                                                  │
│  ┌─────────────┐    ┌──────────────────────────────────────────┐ │
│  │   Signal    │───▶│         LangGraph Orchestrator           │ │
│  │   Router    │    │                                          │ │
│  └─────────────┘    │  explore → fix → build → validate → pr  │ │
│                     │         ↑________________↓               │ │
│  ┌─────────────┐    │              (retry loop)                │ │
│  │  Checkpoint │◀──▶│                                          │ │
│  │   Store     │    └──────────────┬───────────────────────────┘ │
│  └─────────────┘                  │                              │
│                                   ▼                              │
│                     ┌─────────────────────────┐                  │
│                     │   Claude Agent SDK       │                  │
│                     │   (worker nodes)         │                  │
│                     │   Read, Grep, Edit, Bash │                  │
│                     └─────────────────────────┘                  │
│                                   │                              │
│                    ┌──────────────▼──────────────┐               │
│                    │  Validator (caller-injected) │               │
│                    │  returns ValidationResult    │               │
│                    └─────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────┘
                       │
                       ▼
              GitHub / GitLab PR
```

---

## 5. Core Abstractions

### 5.1 Signal

A `Signal` represents the reason a fix run is triggered. It is the entry point into the pipeline.

```
Signal
  ├── ErrorSignal        — exception trace, stack frame, log line
  ├── MetricSignal       — metric name, current value, threshold, direction
  ├── ScheduledSignal    — cron expression, scope hint (full repo or module)
  └── ManualSignal       — free-text description of what to improve
```

Signals carry enough context for the AI agent to know *where to look* and *what problem to solve*.

---

### 5.2 SelfixConfig

The single object the caller constructs and passes to `selfix.run()`.

```
SelfixConfig
  ├── repo_path          — local path or remote URL to clone
  ├── signal             — Signal instance
  ├── validator          — SelfixValidator instance (caller-injected)
  ├── max_attempts       — retry limit before giving up (default: 3)
  ├── pr_config          — PRConfig (branch naming, labels, reviewers)
  ├── agent_config       — AgentConfig (model, token budget, tool allowlist)
  └── checkpoint_dir     — path for LangGraph state persistence
```

---

### 5.3 SelfixValidator (Protocol)

The single extension point. The caller implements this Protocol.

```python
class SelfixValidator(Protocol):
    async def validate(
        self,
        repo_path: str,
        context: FixContext,
    ) -> ValidationResult:
        ...
```

`FixContext` gives the validator full context: the original signal, the diff applied, the attempt number, and any metadata the agent produced.

`ValidationResult` carries:
- `passed: bool` — did it meet the criterion?
- `score: float` — quantitative measure (coverage %, Sharpe ratio, latency ms)
- `feedback: str` — natural language fed back to the agent on retry
- `metadata: dict` — arbitrary structured data for observability

The `feedback` string is the key feedback loop mechanism. On a failed attempt, the agent receives this string as context before generating the next fix. A good validator writes actionable feedback: *"Sharpe improved from 0.8 to 1.05 but max drawdown is 18%, threshold is 15%. Focus on position sizing."*

---

### 5.4 LangGraph Orchestrator (Manager)

The orchestrator owns the pipeline state machine. It knows nothing about the language of the repo, the nature of the validation, or the content of the fix. It only knows:

- What node to run next
- Whether to retry or proceed
- How to checkpoint and resume state
- When to open a PR

**Graph nodes:**

| Node | Responsibility |
|---|---|
| `signal_intake` | Parse and enrich the incoming signal |
| `repo_setup` | Clone or sync the repo, create a fix branch |
| `exploration` | Claude agent explores the repo, produces a context summary |
| `fix_generation` | Claude agent produces and applies a fix |
| `build_check` | Optional fast build/compile gate before full validation |
| `validation` | Call `validator.validate()`, receive `ValidationResult` |
| `retry_decision` | Conditional edge: retry, escalate, or proceed |
| `pr_creation` | Open PR with diff, agent reasoning, and validation report |
| `escalation` | Notify caller that max attempts reached without success |

**Conditional edges:**

```
validation → [passed]  → pr_creation
           → [failed, attempts < max]  → fix_generation
           → [failed, attempts >= max] → escalation
```

---

### 5.5 Claude Agent SDK (Worker)

The worker operates inside `exploration`, `fix_generation`, and `build_check` nodes. It receives a structured prompt from LangGraph and returns a result. It has no knowledge of the graph.

Tools available to the worker:

| Tool | Purpose |
|---|---|
| `Read` | Read file contents |
| `Glob` | Find files matching patterns |
| `Grep` | Search across the codebase |
| `Edit` | Apply targeted file edits |
| `Bash` | Run build, test, lint commands |
| `WebFetch` | Fetch docs, changelogs, CVEs if needed |

The worker's context window receives: the signal, the repo structure, previous attempt feedback, and the task for this node. It returns: a structured result (exploration summary, or applied diff, or build output).

---

## 6. Data Flow — Happy Path

```
1.  Signal fires (cron, webhook, manual)
2.  LangGraph initialises pipeline state, checkpoints
3.  repo_setup: branch created (selfix/fix-<signal-id>)
4.  exploration: Claude reads repo, identifies root cause / opportunity
5.  fix_generation: Claude edits files, produces diff
6.  build_check: fast compile/lint (optional, language-specific)
7.  validation: caller validator runs, returns ValidationResult(passed=True)
8.  pr_creation: PR opened with diff + agent reasoning + validation score
9.  Pipeline completes, state archived
```

---

## 7. Data Flow — Retry Path

```
...
5.  fix_generation (attempt 1): Claude applies fix
6.  validation: ValidationResult(passed=False, feedback="...")
7.  retry_decision: attempts=1 < max_attempts=3 → back to fix_generation
8.  fix_generation (attempt 2): Claude receives previous feedback, applies revised fix
9.  validation: ValidationResult(passed=True)
10. pr_creation
```

---

## 8. Package Architecture

```
selfix/
├── __init__.py              # public API: selfix.run(), selfix.run_async()
├── config.py                # SelfixConfig, AgentConfig, PRConfig
├── signals/
│   ├── base.py              # Signal base class
│   ├── error.py             # ErrorSignal
│   ├── metric.py            # MetricSignal
│   ├── scheduled.py         # ScheduledSignal
│   └── manual.py            # ManualSignal
├── validator/
│   ├── protocol.py          # SelfixValidator Protocol, ValidationResult, FixContext
│   └── builtin/
│       ├── shell.py         # ShellCommandValidator (runs any shell command)
│       ├── pytest.py        # PytestValidator
│       ├── composite.py     # CompositeValidator (AND / OR logic)
│       └── http.py          # HttpHealthValidator
├── graph/
│   ├── orchestrator.py      # LangGraph graph definition
│   ├── state.py             # PipelineState TypedDict
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
│   ├── worker.py            # Claude Agent SDK wrapper
│   └── prompts.py           # Prompt templates per node
├── git/
│   ├── repo.py              # Clone, branch, diff, commit
│   └── pr.py                # GitHub / GitLab PR creation via API
└── observability/
    ├── tracing.py           # LangSmith integration
    └── events.py            # Structured event log
```

---

## 9. Key Design Decisions

### 9.1 Validator as a Protocol, not a base class
Using `typing.Protocol` means callers never import from Selfix to implement a validator. Any object with a matching `validate()` signature works. Zero coupling.

### 9.2 Feedback as the retry fuel
The `feedback` string from `ValidationResult` is the primary mechanism for iterative improvement. The richer the feedback, the better the agent's next attempt. Selfix documents this contract clearly so validator authors write useful feedback, not just pass/fail booleans.

### 9.3 LangGraph owns all state
No mutable state exists outside LangGraph's `PipelineState`. Every node is a pure function: `(state) → state`. This is what makes the pipeline resumable and testable.

### 9.4 Claude Agent SDK is stateless from Selfix's perspective
The worker is called fresh each time. The relevant history (signal, previous diffs, feedback) is passed explicitly in the prompt. The agent does not maintain memory across node calls.

### 9.5 Build check as a fast gate
Running the full validator (especially a backtest or load test) is expensive. An optional `build_check` node runs a cheap compile/lint step first, catching syntax errors before wasting time on validation.

### 9.6 Git operations are isolated to a branch
All edits happen on a `selfix/fix-<signal-id>` branch. The main branch is never touched. If the pipeline fails or escalates, the branch is either deleted or left for manual inspection — configurable.

---

## 10. Phased Delivery Plan

| Phase | Scope | Outcome |
|---|---|---|
| **Phase 1** ✅ | Core pipeline — manual signal, shell validator, single repo, happy path | Working end-to-end pipeline for one language |
| **Phase 2** ✅ | Retry loop, feedback injection, validator protocol, built-in validators | Reliable autonomous improvement with configurable validation |
| **Phase 3** ✅ | Signal router (error, metric, cron), remote repo, PR creation, webhook listener | Production-ready autonomous pipeline |
| **Phase 4** | Observability, LangSmith tracing, structured event log, CLI | Debuggable, packageable, publishable |
| **Phase 5** | Multi-repo, parallel signal queuing, custom agent config per signal | Scale and extensibility |

---

## 11. Technology Stack

| Concern | Technology | Rationale |
|---|---|---|
| Orchestration | LangGraph 1.x | Production-grade stateful graph, checkpointing, HITL |
| AI Worker | Claude Agent SDK (Anthropic) | Native file/shell tools, 1M context, MCP support |
| AI Model | Claude Opus 4.6 | Best reasoning for complex multi-file code changes |
| Git operations | GitPython + GitHub REST API | Mature, well-documented |
| Observability | LangSmith | Native LangGraph integration, trace every node |
| Packaging | Python (pip / uv) | Matches LangGraph and Claude SDK ecosystems |
| Config validation | Pydantic v2 | Runtime validation of SelfixConfig |

---

*Document version: 0.1 — Pre-implementation design*  
*Status: Draft*
