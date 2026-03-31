import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { LLMProvider } from "../diagnose/types.js";
import type { DiagnosisResult } from "../diagnose/types.js";
import type { PatchConfig, PatchResult } from "./types.js";

export * from "./types.js";

const SYSTEM_PROMPT = `\
You are a senior Node.js engineer writing a minimal, surgical code fix.

You will receive:
1. A diagnosis of the root cause.
2. Relevant file slices (filename + line range + content).

Return ONLY valid JSON (no markdown fences) matching this schema:
{
  "diff": "<unified diff string — empty string if no code change is needed>",
  "affectedFiles": ["<relative/path/to/file.ts>", ...],
  "rationale": "<one-paragraph explanation of the change>"
}

Diff format rules:
- Standard unified diff (--- a/file +++ b/file @@ … @@).
- Patch only what is necessary — do not reformat or refactor unrelated code.
- Prefer the simplest correct fix.
- If the fix requires a new file, include it as a new-file diff (--- /dev/null +++ b/path).`;

/**
 * Read a file from disk and return a context window around the given line.
 * Returns `null` if the file cannot be read.
 */
async function sliceFile(
  repoRoot: string,
  relPath: string,
  anchorLine: number | undefined,
  contextLines: number
): Promise<{ path: string; startLine: number; content: string } | null> {
  try {
    const abs = resolve(repoRoot, relPath);
    const text = await readFile(abs, "utf8");
    const lines = text.split("\n");

    const anchor =
      anchorLine !== undefined
        ? Math.min(Math.max(anchorLine - 1, 0), lines.length - 1)
        : 0;

    const start = Math.max(0, anchor - contextLines);
    const end = Math.min(lines.length, anchor + contextLines + 1);

    return {
      path: relPath,
      startLine: start + 1,
      content: lines
        .slice(start, end)
        .map((l, i) => `${start + i + 1}: ${l}`)
        .join("\n"),
    };
  } catch {
    return null;
  }
}

function buildFileContext(
  slices: Array<{ path: string; startLine: number; content: string }>,
  maxChars: number
): string {
  const parts: string[] = [];
  let total = 0;
  for (const slice of slices) {
    const block = `### ${slice.path} (from line ${slice.startLine})\n${slice.content}`;
    if (total + block.length > maxChars) break;
    parts.push(block);
    total += block.length;
  }
  return parts.join("\n\n");
}

interface RawPatch {
  diff: string;
  affectedFiles: string[];
  rationale: string;
}

/**
 * Patch generator — reads minimal file context from disk and asks the LLM
 * to produce a unified diff.
 */
export class PatchGenerator {
  private readonly llm: LLMProvider;
  private readonly repoRoot: string;
  private readonly config: Required<PatchConfig>;

  constructor(llm: LLMProvider, repoRoot: string, config: PatchConfig = {}) {
    this.llm = llm;
    this.repoRoot = repoRoot;
    this.config = {
      contextLines: config.contextLines ?? 10,
      maxContextChars: config.maxContextChars ?? 12_000,
      systemPromptPrefix: config.systemPromptPrefix ?? "",
    };
  }

  async generate(diagnosis: DiagnosisResult): Promise<PatchResult> {
    const anchorLine =
      diagnosis.trigger.kind === "error" ? diagnosis.trigger.line : undefined;

    const slices = (
      await Promise.all(
        diagnosis.suspectedFiles.map((f) =>
          sliceFile(this.repoRoot, f, anchorLine, this.config.contextLines)
        )
      )
    ).filter((s): s is NonNullable<typeof s> => s !== null);

    const fileContext = buildFileContext(slices, this.config.maxContextChars);

    const systemPrompt = this.config.systemPromptPrefix
      ? `${this.config.systemPromptPrefix}\n\n${SYSTEM_PROMPT}`
      : SYSTEM_PROMPT;

    const userPrompt = `
## Diagnosis
Category: ${diagnosis.category}
Severity: ${diagnosis.severity}
Explanation: ${diagnosis.explanation}
Context: ${JSON.stringify(diagnosis.context)}

## File context
${fileContext || "(no file context available)"}
`.trim();

    const raw = await this.llm.complete(systemPrompt, userPrompt, {
      temperature: 0.1,
      maxTokens: 4096,
    });

    let parsed: RawPatch;
    try {
      parsed = JSON.parse(raw) as RawPatch;
    } catch {
      parsed = {
        diff: "",
        affectedFiles: diagnosis.suspectedFiles,
        rationale: raw,
      };
    }

    return {
      diagnosis,
      diff: parsed.diff ?? "",
      affectedFiles: Array.isArray(parsed.affectedFiles)
        ? parsed.affectedFiles
        : [],
      rationale: parsed.rationale ?? "",
    };
  }
}
