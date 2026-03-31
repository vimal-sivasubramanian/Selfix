import type { Trigger } from "../sink/types.js";
import type { FilterConfig, FilterRule } from "./types.js";

export * from "./types.js";

function defaultFingerprint(trigger: Trigger): string {
  switch (trigger.kind) {
    case "error":
      return `error:${trigger.message}:${trigger.file ?? ""}:${trigger.line ?? ""}`;
    case "log":
      return `log:${trigger.level}:${trigger.message}`;
    case "metric":
      return `metric:${trigger.name}:${trigger.threshold}`;
  }
}

interface RuleState {
  /** fingerprint → expiry timestamp */
  dedupeCache: Map<string, number>;
  /** window-start timestamp → count of triggers let through */
  throttleWindow: { start: number; count: number };
}

/**
 * Stateful filter that applies a set of `FilterRule`s to the trigger stream.
 * Each rule maintains independent dedup and throttle state.
 */
export class Filter {
  private readonly rules: FilterRule[];
  private readonly state: Map<number, RuleState> = new Map();

  constructor(config: FilterConfig) {
    this.rules = config.rules;
    for (let i = 0; i < config.rules.length; i++) {
      this.state.set(i, {
        dedupeCache: new Map(),
        throttleWindow: { start: Date.now(), count: 0 },
      });
    }
  }

  /**
   * Returns `true` when the trigger should proceed to the next pipeline stage.
   * Returns `false` when it should be dropped.
   */
  accept(trigger: Trigger): boolean {
    for (let i = 0; i < this.rules.length; i++) {
      const rule = this.rules[i]!;

      if (rule.kinds && !rule.kinds.includes(trigger.kind)) continue;

      const state = this.state.get(i)!;
      const fp = rule.fingerprint
        ? rule.fingerprint(trigger)
        : defaultFingerprint(trigger);

      // --- deduplication ---
      if (rule.dedupeWindowMs && rule.dedupeWindowMs > 0) {
        const expiry = state.dedupeCache.get(fp);
        if (expiry !== undefined && Date.now() < expiry) {
          return false;
        }
        state.dedupeCache.set(fp, Date.now() + rule.dedupeWindowMs);
      }

      // --- throttling ---
      if (rule.maxPerWindow !== undefined && rule.maxPerWindow > 0) {
        const windowMs = rule.throttleWindowMs ?? 60_000;
        const now = Date.now();
        if (now - state.throttleWindow.start > windowMs) {
          state.throttleWindow = { start: now, count: 0 };
        }
        if (state.throttleWindow.count >= rule.maxPerWindow) {
          return false;
        }
        state.throttleWindow.count++;
      }
    }

    return true;
  }

  /** Evict expired dedup entries across all rule states (call periodically). */
  gc(): void {
    const now = Date.now();
    for (const state of this.state.values()) {
      for (const [key, expiry] of state.dedupeCache) {
        if (now >= expiry) state.dedupeCache.delete(key);
      }
    }
  }
}
