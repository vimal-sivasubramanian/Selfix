import { randomUUID } from "node:crypto";
import { Sink } from "./sink/index.js";
import { Filter } from "./filter/index.js";
import { DiagnoseAgent } from "./diagnose/index.js";
import { PatchGenerator } from "./patch/index.js";
import { ValidationLayer } from "./validate/index.js";
import { CommitAndPR } from "./commit/index.js";
import type { Trigger } from "./sink/types.js";
import type { SelfixConfig, PipelineRunResult } from "./config.js";

export { SelfixConfig, PipelineRunResult };

/**
 * SelfixPipeline orchestrates all six stages:
 *
 *   Sink → Filter → Diagnose → Patch → Validate → Commit/PR
 *
 * Call `start()` once to begin listening. Call `stop()` to shut down cleanly.
 */
export class SelfixPipeline {
  private readonly sink: Sink;
  private readonly filter: Filter;
  private readonly diagnose: DiagnoseAgent;
  private readonly patcher: PatchGenerator;
  private readonly validation: ValidationLayer;
  private readonly committer: CommitAndPR;
  private readonly config: SelfixConfig;

  /** Tracks in-flight pipeline runs to avoid concurrent patches on the same trigger. */
  private readonly inflight: Set<string> = new Set();

  private gcTimer?: ReturnType<typeof setInterval>;
  private unsubscribe?: () => void;

  constructor(config: SelfixConfig) {
    this.config = config;

    this.sink = new Sink(config.sources);
    this.filter = new Filter(config.filter);
    this.diagnose = new DiagnoseAgent(config.llm, config.diagnose);
    this.patcher = new PatchGenerator(
      config.llm,
      config.repoRoot,
      config.patch
    );
    this.validation = new ValidationLayer(config.validator);
    this.committer = new CommitAndPR(config.repoRoot, config.commit);
  }

  async start(): Promise<void> {
    // Garbage-collect filter state every 5 minutes
    this.gcTimer = setInterval(() => this.filter.gc(), 5 * 60 * 1_000);

    this.unsubscribe = this.sink.onTrigger((trigger) => {
      void this.handleTrigger(trigger);
    });

    await this.sink.start();
  }

  async stop(): Promise<void> {
    if (this.gcTimer) clearInterval(this.gcTimer);
    this.unsubscribe?.();
    await this.sink.stop();
  }

  private async handleTrigger(trigger: Trigger): Promise<void> {
    // Drop noisy / duplicate triggers
    if (!this.filter.accept(trigger)) return;

    const triggerId = randomUUID();
    const startedAt = new Date().toISOString();

    // Prevent concurrent runs
    const key = `${trigger.kind}:${triggerId}`;
    if (this.inflight.has(key)) return;
    this.inflight.add(key);

    let result: PipelineRunResult;
    try {
      result = await this.runPipeline(trigger, triggerId, startedAt);
    } catch (err) {
      result = {
        triggerId,
        status: "error",
        startedAt,
        finishedAt: new Date().toISOString(),
        reason: String(err),
      };
    } finally {
      this.inflight.delete(key);
    }

    if (this.config.onResult) {
      await this.config.onResult(result);
    }
  }

  private async runPipeline(
    trigger: Trigger,
    triggerId: string,
    startedAt: string
  ): Promise<PipelineRunResult> {
    // --- Stage 2: Diagnose ---
    const diagnosis = await this.diagnose.diagnose(trigger);

    if (diagnosis.suspectedFiles.length === 0) {
      return {
        triggerId,
        status: "skipped",
        startedAt,
        finishedAt: new Date().toISOString(),
        reason: "No suspected files — cannot generate patch.",
        details: { category: diagnosis.category, severity: diagnosis.severity },
      };
    }

    // --- Stage 3: Patch ---
    const patch = await this.patcher.generate(diagnosis);

    if (!patch.diff.trim()) {
      return {
        triggerId,
        status: "skipped",
        startedAt,
        finishedAt: new Date().toISOString(),
        reason: "LLM returned empty diff.",
        details: { rationale: patch.rationale },
      };
    }

    // --- Stage 4: Baseline validation (pre-patch) ---
    const baseline = await this.validation.baseline(this.config.repoRoot);

    // --- Stage 5 + 6: Commit + validate + PR ---
    const outcome = await this.committer.run(patch, async (p) =>
      this.validation.validate(this.config.repoRoot, p, baseline.score)
    );

    const finishedAt = new Date().toISOString();

    if (outcome.success) {
      return {
        triggerId,
        status: "patched",
        startedAt,
        finishedAt,
        prUrl: outcome.prUrl,
        details: {
          branch: outcome.branch,
          score: outcome.validation.score,
          baselineScore: outcome.validation.baselineScore,
        },
      };
    }

    return {
      triggerId,
      status: "reverted",
      startedAt,
      finishedAt,
      reason: outcome.reason,
      details: {
        score: outcome.validation.score,
        baselineScore: outcome.validation.baselineScore,
        summary: outcome.validation.summary,
      },
    };
  }
}
