import type { Trigger } from "../sink/types.js";
import type {
  DiagnoseConfig,
  DiagnosisResult,
  LLMProvider,
  RootCauseCategory,
  Severity,
} from "./types.js";

export * from "./types.js";

const SYSTEM_PROMPT = `\
You are a senior Node.js reliability engineer.
Your job is to analyse an error or anomaly signal and produce a structured diagnosis.

Return ONLY valid JSON (no markdown fences) matching this schema:
{
  "category": "<null_reference|type_mismatch|unhandled_promise|network_timeout|resource_exhaustion|logic_error|config_error|dependency_failure|unknown>",
  "severity": "<low|medium|high|critical>",
  "explanation": "<one-paragraph human explanation>",
  "suspectedFiles": ["<relative/path/to/file.ts>", ...],
  "context": { "<key>": "<value>", ... }
}

Rules:
- suspectedFiles must be relative paths from the repo root.
- List the most likely file first.
- context should capture anything useful for a patch generator (variable names, function names, line numbers).
- Be concise and precise.`;

function truncateStack(stack: string, maxLines: number): string {
  return stack.split("\n").slice(0, maxLines).join("\n");
}

function buildUserPrompt(trigger: Trigger, maxStackLines: number): string {
  switch (trigger.kind) {
    case "error": {
      const stackPart = trigger.stack
        ? `\nStack trace:\n${truncateStack(trigger.stack, maxStackLines)}`
        : "";
      const filePart = trigger.file
        ? `\nFile: ${trigger.file}${trigger.line ? `:${trigger.line}` : ""}`
        : "";
      return `Error signal received.\nMessage: ${trigger.message}${filePart}${stackPart}`;
    }
    case "log":
      return `Log signal received.\nLevel: ${trigger.level}\nMessage: ${trigger.message}`;
    case "metric":
      return `Metric threshold breached.\nMetric: ${trigger.name}\nValue: ${trigger.value} (threshold: ${trigger.threshold})`;
  }
}

interface RawDiagnosis {
  category: RootCauseCategory;
  severity: Severity;
  explanation: string;
  suspectedFiles: string[];
  context: Record<string, unknown>;
}

/**
 * Diagnose stage — feeds a trigger to the LLM and returns a structured
 * `DiagnosisResult` with root-cause classification and suspected files.
 */
export class DiagnoseAgent {
  private readonly llm: LLMProvider;
  private readonly config: Required<DiagnoseConfig>;

  constructor(llm: LLMProvider, config: DiagnoseConfig = {}) {
    this.llm = llm;
    this.config = {
      maxStackLines: config.maxStackLines ?? 20,
      systemPromptPrefix: config.systemPromptPrefix ?? "",
    };
  }

  async diagnose(trigger: Trigger): Promise<DiagnosisResult> {
    const systemPrompt = this.config.systemPromptPrefix
      ? `${this.config.systemPromptPrefix}\n\n${SYSTEM_PROMPT}`
      : SYSTEM_PROMPT;

    const userPrompt = buildUserPrompt(trigger, this.config.maxStackLines);

    const raw = await this.llm.complete(systemPrompt, userPrompt, {
      temperature: 0.1,
      maxTokens: 1024,
    });

    let parsed: RawDiagnosis;
    try {
      parsed = JSON.parse(raw) as RawDiagnosis;
    } catch {
      // Fallback if the LLM returns malformed JSON
      parsed = {
        category: "unknown",
        severity: "medium",
        explanation: raw,
        suspectedFiles: [],
        context: {},
      };
    }

    return {
      trigger,
      category: parsed.category ?? "unknown",
      severity: parsed.severity ?? "medium",
      explanation: parsed.explanation ?? "",
      suspectedFiles: Array.isArray(parsed.suspectedFiles)
        ? parsed.suspectedFiles
        : [],
      context: parsed.context ?? {},
    };
  }
}
