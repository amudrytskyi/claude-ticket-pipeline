---
name: review-code
description: Phase 4 of 5 of the ticket pipeline. Reviews the implemented change against the plan and closes out every acceptance criterion with evidence. Runs no test suites; verification does that. Reports only. Run it as /review-code <ticket>.
argument-hint: <ticket> [artifact URLs] [file paths]
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-code-reviewer-artifacts
# allowed-tools only pre-approves these tools for the turn that runs this skill.
# It restricts nothing. Use permission rules or disallowed-tools to restrict.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Edit
  - WebFetch
---

# Phase 4 of 5: close out every acceptance criterion

Pipeline: `/plan-ticket` → `/review-plan` → `/implement-plan` → `/review-code` (this) → `/verify-ticket`.
You did not write this code; green tests are not evidence that the criteria are met. Each
invocation is one round and runs as a fresh agent.

## 0. Load the project profile

Read `.claude/pipeline.local.md`. If it is missing, stop and ask the user to create it.

## 1. Take a snapshot of the change, including new files

- Read `<TICKET>-plan.md` (§2 is the authoritative list of criteria), `-plan-review.md`,
  `-impl-findings.md`, then the ticket.
- Record `git rev-parse HEAD` before you start. The change is the tracked edits **and** the new files:
  - `git status --porcelain`
  - `git diff <base branch>`
  - `git ls-files --others --exclude-standard`

  `git diff` alone does not show untracked files, and new tests and new migrations are usually
  untracked until someone commits them. Read every new file in full.
- Before you write the verdict, check `git rev-parse HEAD` and `git status --porcelain` again. If either
  changed (someone committed or edited during the review), collect the change again from the base,
  re-check the criteria it touches, and say so in the review.

## 2. Close out each criterion

Build a table with one row per criterion in §2: the `file:line` that satisfies it, the test that
would fail without it, and CLOSED, PARTIAL or NOT CLOSED. A criterion with no test that exercises it
is at best PARTIAL. Accept what the diff shows, not what the plan says.

Do **not** run the test suites; `/verify-ticket` runs the full matrix once on the final tree. Run a
single test only when you cannot decide a criterion without it, and name it in the table.

## 3. Check what the criteria do not cover

- each profile invariant, in the shipped code;
- races, error and partial-failure paths, retries, resource leaks, and anything the change breaks
  in a neighbouring process;
- tests that would pass without the change (a test that cannot fail proves nothing);
- the profile's docs and release-note obligations.

## 4. Write the review

Append to `<work>/<TICKET>-code-review.md` under `## Round N — <date>`: the HEAD you reviewed, the
criteria table, and numbered findings (severity, `file:line`, the defect in one sentence, a failure
scenario, evidence, verdict, the exact fix). On later rounds, first re-check every earlier finding:
resolved, still open or regressed. End the round with the verdict line: `Verdict: ALL ACS CLOSED` or
`Verdict: GAPS`.

## Rules

- Report only. Fixes go back through `/implement-plan`.
- Never edit a file in place (`sed -i`, `perl -i`, `prettier --write`, `eslint --fix`), not even a scratch copy: the guard refuses it for this role whatever the path. Write a changed scratch file whole, with `Write` or a heredoc to an absolute path.
- Never commit, branch or push, and never post to the ticket system or PR unless the user asks in
  this conversation.

## Verdict

**ALL ACS CLOSED**: every criterion CLOSED and no open finding. Otherwise **GAPS**, with the open
findings by severity and the criteria not yet closed. On GAPS the next step is `/implement-plan
<TICKET>`, then `/review-code <TICKET>` again in a new session. On ALL ACS CLOSED it is
`/verify-ticket <TICKET>`.
