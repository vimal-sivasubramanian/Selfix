# selfix

Autonomous error detection, diagnosis, and patch generation pipeline for Node.js apps.

When an error or anomaly is detected, selfix:
1. **Classifies** the root cause via an LLM
2. **Proposes** a unified diff fix using only the relevant file slice
3. **Validates** the patch against your test suite
4. **Opens a PR** (or reverts) automatically

---

## Pipeline

```
 ┌──────────┐   triggers   ┌──────────┐   pass   ┌──────────────┐
 │  Sink    │─────────────▶│  Filter  │─────────▶│   Diagnose   │
 │(sources) │              │ (dedup / │          │   (LLM →     │
 └──────────┘              │ throttle)│          │  DiagResult) │
                           └──────────┘          └──────┬───────┘
                                                        │ suspectedFiles
                                                        ▼
                                               ┌────────────────┐
                                               │ Patch Generator│
                                               │ (file slice +  │
                                               │  LLM → diff)   │
                                               └──────┬─────────┘
                                                      │ unified diff
                                                      ▼
                                            ┌──────────────────┐
                                            │ Validation Layer │
                                            │ (caller Validator│
                                            │  baseline + post)│
                                            └──────┬───────────┘
                                                   │ ValidationResult
                                          pass ◀───┴───▶ fail
                                            │               │
                                            ▼               ▼
                                    ┌──────────────┐  ┌──────────┐
                                    │  Commit + PR │  │  Revert  │
                                    │  (simple-git │  │ + delete │
                                    │  + GitHub)   │  │  branch  │
                                    └──────────────┘  └──────────┘
```

---

## Installation

```bash
npm install selfix simple-git
```

---

## Quick start

### 1. Copy the example config

```bash
cp node_modules/selfix/selfix.config.example.ts selfix.config.ts
```

Fill in:
- Your `DataSource` adapter (wraps `uncaughtException`, Sentry, Datadog, etc.)
- Your `LLMProvider` (Anthropic, OpenAI, or any model with a text completion interface)
- Your `Validator` (runs `npm test`, returns a 0–1 score)
- `commit.githubRepo` and `commit.githubToken`

### 2. Start the pipeline

```ts
import { SelfixPipeline } from "selfix";
import config from "./selfix.config.js";

const pipeline = new SelfixPipeline(config);
await pipeline.start();

// pipeline.stop() to shut down cleanly
```

---

## Core interfaces

### `DataSource`

Feed triggers into the pipeline from any source.

```ts
interface DataSource {
  start(emit: (trigger: Trigger) => void): void | Promise<void>;
  stop(): void | Promise<void>;
}
```

`emit` accepts `ErrorTrigger | LogTrigger | MetricTrigger`.

---

### `LLMProvider`

Back the diagnose and patch stages with any model.

```ts
interface LLMProvider {
  complete(
    systemPrompt: string,
    userPrompt: string,
    options?: LLMCallOptions
  ): Promise<string>;
}
```

The LLM receives **only a slice** of the relevant file (configurable via `patch.contextLines` and `patch.maxContextChars`) — not the entire repository.

---

### `Validator`

Plug in your test suite. Called twice per run: baseline (pre-patch) and patched.

```ts
interface Validator {
  validate(repoRoot: string, context: ValidationContext): Promise<ValidatorOutput>;
}

interface ValidatorOutput {
  score: number;   // 0.0 – 1.0
  summary: string;
  details?: Record<string, unknown>;
}
```

The patch is accepted only when `patchedScore >= baselineScore`.

---

## Config reference

```ts
interface SelfixConfig {
  repoRoot: string;           // absolute path to repo
  sources: DataSource[];      // one or more signal adapters
  filter: FilterConfig;       // dedup + throttle rules
  llm: LLMProvider;           // LLM backend
  diagnose?: DiagnoseConfig;  // stack line limit, system prompt prefix
  patch?: PatchConfig;        // context lines, max chars
  validator: Validator;       // your test runner
  commit: CommitConfig;       // branch prefix, github token/repo
  onResult?: (r: PipelineRunResult) => void | Promise<void>;
}
```

### `FilterConfig`

```ts
interface FilterRule {
  kinds?: TriggerKind[];          // "error" | "log" | "metric"
  dedupeWindowMs?: number;        // silence identical triggers for N ms
  maxPerWindow?: number;          // max N triggers per throttleWindowMs
  throttleWindowMs?: number;      // default 60_000
  fingerprint?: (t: Trigger) => string;
}
```

### `CommitConfig`

| Field | Default | Description |
|---|---|---|
| `baseBranch` | `"main"` | Branch to fork from |
| `branchPrefix` | `"selfix"` | Fix branch name prefix |
| `githubToken` | required | PAT with `repo` scope |
| `githubRepo` | required | `"owner/repo"` |
| `draftPr` | `false` | Open as draft PR |
| `prLabel` | `"selfix"` | Label attached to the PR |

---

## Design constraints

- **Config-driven** — all behaviour is wired through a typed `SelfixConfig` object; nothing is hardcoded.
- **Injected adapters** — `DataSource`, `LLMProvider`, and `Validator` are caller-supplied; selfix ships no concrete implementations.
- **Minimal LLM context** — the patch stage reads only the relevant lines around the suspected defect, capped at `maxContextChars`.
- **Temp branches only** — patches are always applied on a `selfix/<slug>` branch. `main` is never touched directly.
- **Comparable scores** — `ValidationResult.score` and `ValidationResult.baselineScore` are both 0–1 floats so the pipeline can make an objective accept/revert decision.

---

## License

MIT
