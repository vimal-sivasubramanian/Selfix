import type { ValidationResult } from "../validate/types.js";

export interface CommitConfig {
  /**
   * The base branch to branch off from (e.g. "main").
   * Default: "main".
   */
  baseBranch?: string;

  /**
   * Prefix for fix branches.
   * The full branch name will be `<prefix>/<slug>`.
   * Default: "selfix".
   */
  branchPrefix?: string;

  /**
   * GitHub personal access token (or fine-grained token) with `repo` scope.
   * Required for PR creation.
   */
  githubToken: string;

  /**
   * GitHub repository in `owner/repo` format.
   * Required for PR creation.
   */
  githubRepo: string;

  /**
   * PR label to attach. Default: "selfix".
   */
  prLabel?: string;

  /**
   * If true, the pipeline will open a draft PR instead of a regular one.
   * Default: false.
   */
  draftPr?: boolean;
}

export interface CommitResult {
  success: true;
  branch: string;
  prUrl: string;
  validation: ValidationResult;
}

export interface RevertResult {
  success: false;
  reason: string;
  validation: ValidationResult;
}

export type CommitOutcome = CommitResult | RevertResult;
