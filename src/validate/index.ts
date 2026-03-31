import type { PatchResult } from "../patch/types.js";
import type {
  ValidationResult,
  Validator,
  ValidatorOutput,
} from "./types.js";

export * from "./types.js";

/**
 * Validation layer — runs the caller-supplied `Validator` twice (baseline and
 * patched) and returns a `ValidationResult` with a comparable numeric score.
 */
export class ValidationLayer {
  private readonly validator: Validator;

  constructor(validator: Validator) {
    this.validator = validator;
  }

  /**
   * Run baseline validation against the current repo state (no patch applied).
   */
  async baseline(repoRoot: string): Promise<ValidatorOutput> {
    return this.validator.validate(repoRoot, { phase: "baseline" });
  }

  /**
   * Run post-patch validation.
   * The patch must already be applied to disk before calling this method.
   */
  async validate(
    repoRoot: string,
    patch: PatchResult,
    baselineScore: number
  ): Promise<ValidationResult> {
    const result = await this.validator.validate(repoRoot, {
      phase: "patched",
      patch,
    });

    const out: ValidationResult = {
      patch,
      score: result.score,
      baselineScore,
      passed: result.score >= baselineScore,
      summary: result.summary,
    };
    if (result.details !== undefined) out.details = result.details;
    return out;
  }
}
