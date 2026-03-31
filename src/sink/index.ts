import type { DataSource, Trigger } from "./types.js";

export * from "./types.js";

/**
 * Sink wraps one or more DataSource adapters and fans their output into a
 * single async stream that the rest of the pipeline can consume.
 */
export class Sink {
  private readonly sources: DataSource[];
  private handlers: Array<(trigger: Trigger) => void> = [];
  private running = false;

  constructor(sources: DataSource[]) {
    if (sources.length === 0) {
      throw new Error("Sink requires at least one DataSource");
    }
    this.sources = sources;
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;

    const emit = (trigger: Trigger): void => {
      for (const handler of this.handlers) {
        handler(trigger);
      }
    };

    await Promise.all(this.sources.map((s) => s.start(emit)));
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    this.running = false;
    await Promise.all(this.sources.map((s) => s.stop()));
    this.handlers = [];
  }

  /** Register a listener that receives every incoming trigger. */
  onTrigger(handler: (trigger: Trigger) => void): () => void {
    this.handlers.push(handler);
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler);
    };
  }
}
