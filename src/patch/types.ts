import type { DiagnosisResult } from "../diagnose/types.js";

export interface PatchResult {
  diagnosis: DiagnosisResult;
  /** Unified diff string (git-compatible). Empty string means no patch needed. */
  diff: string;
  /** Files the patch touches, as relative paths. */
  affectedFiles: string[];
  /** LLM's explanation of the proposed change. */
  rationale: string;
}

export interface PatchConfig {
  /**
   * Number of lines of context to include above and below the relevant region
   * when slicing files for the LLM prompt.
   * Default: 10.
   */
  contextLines?: number;
  /**
   * Hard cap on total characters sent to the LLM across all file slices.
   * Default: 12_000.
   */
  maxContextChars?: number;
  /**
   * System prompt prefix for the patch generator.
   */
  systemPromptPrefix?: string;
}
