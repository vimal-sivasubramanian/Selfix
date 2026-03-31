import type { Trigger, TriggerKind } from "../sink/types.js";

export interface FilterRule {
  /**
   * Which trigger kinds this rule applies to.
   * Omit to match all kinds.
   */
  kinds?: TriggerKind[];

  /**
   * Deduplicate triggers whose fingerprint matches within this window (ms).
   * Two triggers are considered duplicates when their fingerprints are equal.
   * Default: 0 (no deduplication).
   */
  dedupeWindowMs?: number;

  /**
   * Maximum number of triggers allowed through per `throttleWindowMs`.
   * Excess triggers are silently dropped.
   */
  maxPerWindow?: number;

  /** Window size for `maxPerWindow` throttling (ms). Default: 60_000. */
  throttleWindowMs?: number;

  /**
   * Custom fingerprint function.
   * Defaults to a hash of `kind + message/name + file`.
   */
  fingerprint?: (trigger: Trigger) => string;
}

export interface FilterConfig {
  rules: FilterRule[];
}
