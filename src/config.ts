import type { DataSource } from "./sink/types.js";
import type { FilterConfig } from "./filter/types.js";
import type { LLMProvider, DiagnoseConfig } from "./diagnose/types.js";
import type { PatchConfig } from "./patch/types.js";
import type { Validator } from "./validate/types.js";
import type { CommitConfig } from "./commit/types.js";

export interface SelfixConfig {
  /**
   * Absolute path to the repository root that selfix will patch.
   */
  repoRoot: string;

  /**
   * One or more data sources that feed triggers into the pipeline.
   */
  sources: DataSource[];

  /**
   * Noise reduction rules applied to every incoming trigger.
   */
  filter: FilterConfig;

  /**
   * LLM provider used by both the diagnose and patch stages.
   */
  llm: LLMProvider;

  /**
   * Fine-tuning for the diagnose stage.
   */
  diagnose?: DiagnoseConfig;

  /**
   * Fine-tuning for the patch generator.
   */
  patch?: PatchConfig;

  /**
   * Caller-supplied test/build runner.
   */
  validator: Validator;

  /**
   * Git + GitHub configuration for branch creation and PR opening.
   */
  commit: CommitConfig;

  /**
   * Called after each pipeline run (success or failure) for observability.
   */
  onResult?: (result: PipelineRunResult) => void | Promise<void>;
}

export interface PipelineRunResult {
  triggerId: string;
  status: "patched" | "reverted" | "skipped" | "error";
  /** ISO timestamp. */
  startedAt: string;
  /** ISO timestamp. */
  finishedAt: string;
  /** PR URL when status is "patched". */
  prUrl?: string;
  /** Reason string when status is "reverted" or "error". */
  reason?: string;
  details?: Record<string, unknown>;
}
