// Pipeline entry point
export { SelfixPipeline } from "./pipeline.js";
export type { SelfixConfig, PipelineRunResult } from "./config.js";

// Sink
export { Sink } from "./sink/index.js";
export type { DataSource, Trigger, TriggerKind, ErrorTrigger, LogTrigger, MetricTrigger } from "./sink/types.js";

// Filter
export { Filter } from "./filter/index.js";
export type { FilterConfig, FilterRule } from "./filter/types.js";

// Diagnose
export { DiagnoseAgent } from "./diagnose/index.js";
export type {
  DiagnoseConfig,
  DiagnosisResult,
  LLMProvider,
  LLMCallOptions,
  RootCauseCategory,
  Severity,
} from "./diagnose/types.js";

// Patch
export { PatchGenerator } from "./patch/index.js";
export type { PatchConfig, PatchResult } from "./patch/types.js";

// Validate
export { ValidationLayer } from "./validate/index.js";
export type {
  Validator,
  ValidatorOutput,
  ValidationContext,
  ValidationResult,
  HealthScore,
} from "./validate/types.js";

// Commit
export { CommitAndPR } from "./commit/index.js";
export type { CommitConfig, CommitOutcome, CommitResult, RevertResult } from "./commit/types.js";
