/**
 * selfix.config.example.ts
 *
 * Copy this file to selfix.config.ts in your project root and fill in the
 * blanks. Everything prefixed with `My` is a placeholder you replace.
 */

import type {
  SelfixConfig,
  DataSource,
  Trigger,
  LLMProvider,
  LLMCallOptions,
  Validator,
  ValidatorOutput,
  ValidationContext,
} from "selfix";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

// ---------------------------------------------------------------------------
// 1. DataSource — adapt your error provider here
// ---------------------------------------------------------------------------

class ProcessErrorSource implements DataSource {
  private emit!: (t: Trigger) => void;

  start(emit: (t: Trigger) => void): void {
    this.emit = emit;

    process.on("uncaughtException", (err: Error) => {
      this.emit({
        kind: "error",
        message: err.message,
        stack: err.stack,
        timestamp: new Date(),
      });
    });

    process.on("unhandledRejection", (reason) => {
      const err = reason instanceof Error ? reason : new Error(String(reason));
      this.emit({
        kind: "error",
        message: err.message,
        stack: err.stack,
        timestamp: new Date(),
      });
    });
  }

  stop(): void {
    process.removeAllListeners("uncaughtException");
    process.removeAllListeners("unhandledRejection");
  }
}

// ---------------------------------------------------------------------------
// 2. LLMProvider — wire up whichever model you use
// ---------------------------------------------------------------------------

class AnthropicProvider implements LLMProvider {
  private readonly apiKey: string;
  private readonly model: string;

  constructor(apiKey: string, model = "claude-opus-4-6") {
    this.apiKey = apiKey;
    this.model = model;
  }

  async complete(
    systemPrompt: string,
    userPrompt: string,
    options: LLMCallOptions = {}
  ): Promise<string> {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": this.apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: this.model,
        max_tokens: options.maxTokens ?? 4096,
        temperature: options.temperature ?? 0.1,
        system: systemPrompt,
        messages: [{ role: "user", content: userPrompt }],
      }),
    });

    if (!res.ok) {
      throw new Error(`Anthropic API error ${res.status}: ${await res.text()}`);
    }

    const data = (await res.json()) as {
      content: Array<{ type: string; text: string }>;
    };

    return data.content
      .filter((b) => b.type === "text")
      .map((b) => b.text)
      .join("");
  }
}

// ---------------------------------------------------------------------------
// 3. Validator — run your test suite and return a 0–1 score
// ---------------------------------------------------------------------------

class NpmTestValidator implements Validator {
  async validate(
    repoRoot: string,
    context: ValidationContext
  ): Promise<ValidatorOutput> {
    try {
      const { stdout, stderr } = await execAsync("npm test -- --reporter=json", {
        cwd: repoRoot,
        timeout: 120_000,
      });

      // Parse Jest / Vitest JSON output for a precise score
      try {
        const report = JSON.parse(stdout) as {
          numPassedTests?: number;
          numTotalTests?: number;
          success?: boolean;
        };
        const total = report.numTotalTests ?? 1;
        const passed = report.numPassedTests ?? (report.success ? total : 0);
        return {
          score: total > 0 ? passed / total : 1,
          summary: `${passed}/${total} tests passed`,
        };
      } catch {
        // Reporter isn't JSON — treat exit 0 as full pass
        return { score: 1, summary: stdout.slice(-500) };
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      // Tests failed — score 0
      return { score: 0, summary: msg.slice(0, 500) };
    }
  }
}

// ---------------------------------------------------------------------------
// 4. Assemble the config
// ---------------------------------------------------------------------------

const config: SelfixConfig = {
  repoRoot: process.cwd(),

  sources: [new ProcessErrorSource()],

  filter: {
    rules: [
      {
        kinds: ["error"],
        dedupeWindowMs: 5 * 60 * 1_000,   // 5-minute dedup per unique error
        maxPerWindow: 3,                    // at most 3 unique errors per minute
        throttleWindowMs: 60_000,
      },
      {
        kinds: ["metric"],
        dedupeWindowMs: 10 * 60 * 1_000,  // 10-minute dedup for metric alerts
      },
    ],
  },

  llm: new AnthropicProvider(process.env["ANTHROPIC_API_KEY"] ?? ""),

  diagnose: {
    maxStackLines: 25,
    systemPromptPrefix: "This is a Node.js/TypeScript microservice.",
  },

  patch: {
    contextLines: 15,
    maxContextChars: 12_000,
  },

  validator: new NpmTestValidator(),

  commit: {
    baseBranch: "main",
    branchPrefix: "selfix",
    githubToken: process.env["GITHUB_TOKEN"] ?? "",
    githubRepo: "your-org/your-repo",
    draftPr: true,
  },

  onResult(result) {
    console.log("[selfix]", result.status, result.triggerId, result.prUrl ?? result.reason ?? "");
  },
};

export default config;
