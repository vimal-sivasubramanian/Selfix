export type TriggerKind = "error" | "log" | "metric";

export interface ErrorTrigger {
  kind: "error";
  message: string;
  stack?: string;
  /** File path where the error originated, if known. */
  file?: string;
  /** Line number, if known. */
  line?: number;
  timestamp: Date;
  /** Arbitrary metadata the DataSource wants to forward. */
  metadata?: Record<string, unknown>;
}

export interface LogTrigger {
  kind: "log";
  level: "warn" | "error" | "fatal";
  message: string;
  timestamp: Date;
  metadata?: Record<string, unknown>;
}

export interface MetricTrigger {
  kind: "metric";
  name: string;
  value: number;
  /** Threshold that was breached. */
  threshold: number;
  timestamp: Date;
  metadata?: Record<string, unknown>;
}

export type Trigger = ErrorTrigger | LogTrigger | MetricTrigger;

/**
 * Implement this interface to feed raw signals into the selfix pipeline.
 *
 * The adapter is responsible for converting provider-specific formats
 * (Sentry events, Datadog alerts, Node `uncaughtException`, etc.) into
 * the canonical `Trigger` shape and calling `emit` for each one.
 */
export interface DataSource {
  /**
   * Called once by the pipeline when it starts.
   * The adapter should start listening / polling here and call `emit`
   * whenever a new trigger arrives.
   */
  start(emit: (trigger: Trigger) => void): void | Promise<void>;

  /** Called when the pipeline shuts down — clean up listeners, timers, etc. */
  stop(): void | Promise<void>;
}
