import type { PatchResult } from "../patch/types.js";

/**
 * Numeric score representing build / test health.
 * 1.0 = all tests pass, 0.0 = nothing passes.
 * Values in between reflect partial passage (e.g. 0.85 = 85% of tests green).
 */
export type HealthScore = number;

export interface ValidationResult {
  patch: PatchResult;
  /** 0.0 – 1.0 score for the state of the repo *after* the patch was applied. */
  score: HealthScore;
  /** Score for the baseline (pre-patch) state. */
  baselineScore: HealthScore;
  /** Whether the patch improved or at least maintained quality. */
  passed: boolean;
  /** Human-readable summary from the validator (test output, lint errors, …). */
  summary: string;
  /** Any structured data the caller's validator wants to forward. */
  details?: Record<string, unknown>;
}

/**
 * Implement this interface to plug your test/lint/build suite into selfix.
 *
 * `validate` is called twice by the pipeline:
 * - once on the HEAD commit before the patch (baseline),
 * - once after `git apply` of the patch.
 *
 * The pipeline passes `context` so validators can distinguish the two calls
 * and, e.g., skip expensive tasks on the baseline run.
 */
export interface Validator {
  validate(
    repoRoot: string,
    context: ValidationContext
  ): Promise<ValidatorOutput>;
}

export interface ValidationContext {
  /** "baseline" = pre-patch run; "patched" = post-patch run. */
  phase: "baseline" | "patched";
  /** The patch being evaluated (available on the "patched" phase). */
  patch?: PatchResult;
}

export interface ValidatorOutput {
  /** 0.0 – 1.0. */
  score: HealthScore;
  summary: string;
  details?: Record<string, unknown>;
}
