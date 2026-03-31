import type { Trigger } from "../sink/types.js";

export type Severity = "low" | "medium" | "high" | "critical";

export type RootCauseCategory =
  | "null_reference"
  | "type_mismatch"
  | "unhandled_promise"
  | "network_timeout"
  | "resource_exhaustion"
  | "logic_error"
  | "config_error"
  | "dependency_failure"
  | "unknown";

export interface DiagnosisResult {
  trigger: Trigger;
  category: RootCauseCategory;
  severity: Severity;
  /** Human-readable explanation from the LLM. */
  explanation: string;
  /**
   * Ordered list of file paths most likely to contain the defect.
   * The patch generator will read these to build its context window.
   */
  suspectedFiles: string[];
  /** Structured context the LLM extracted for downstream stages. */
  context: Record<string, unknown>;
}

/**
 * Implement this interface to back the diagnose stage with any LLM provider
 * (Anthropic, OpenAI, local model, etc.).
 */
export interface LLMProvider {
  /**
   * Send a prompt and receive a text completion.
   *
   * @param systemPrompt  Instructions / role definition.
   * @param userPrompt    The actual request.
   * @param options       Optional per-call overrides (temperature, max tokens…).
   */
  complete(
    systemPrompt: string,
    userPrompt: string,
    options?: LLMCallOptions
  ): Promise<string>;
}

export interface LLMCallOptions {
  temperature?: number;
  maxTokens?: number;
  /** Stop sequences. */
  stop?: string[];
}

export interface DiagnoseConfig {
  /**
   * Maximum number of stack-trace lines to include in the LLM prompt.
   * Default: 20.
   */
  maxStackLines?: number;
  /**
   * System prompt prefix injected before the built-in instructions.
   * Useful for project-specific context ("This is a NestJS app…").
   */
  systemPromptPrefix?: string;
}
