---
name: implement-plan
description: Phase 3 of 5 of the ticket pipeline. Implements one ticket strictly from its reviewed plan, in the checkout the user has open, with fast checks only. Also runs fix rounds after code review, verification or pull-request feedback. Never commits. Run it as /implement-plan <ticket>.
argument-hint: <ticket> [base branch] [artifact URLs] [file paths]
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-implementer-artifacts
# allowed-tools only pre-approves these tools for the turn that runs this skill.
# It restricts nothing. Use permission rules or disallowed-tools to restrict.
# Test commands are not listed here: allow them in your permission settings.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Edit
  - WebFetch
---

# Phase 3 of 5: implement from the plan

Pipeline: `/plan-ticket` → `/review-plan` → `/implement-plan` (this) → `/review-code` → `/verify-ticket`.
`<work>/<TICKET>-plan.md` §2 is the specification; the ticket body is the subset its owner has seen.

## 0. Load the project profile

Read `.claude/pipeline.local.md`. If it is missing, stop and ask the user to create it.

## Input

`$ARGUMENTS`: the ticket (`<TICKET>`, required), an optional base-branch override (otherwise the
plan's §10), optional artifact URLs and file paths. If the plan does not exist, stop.

## 1. Check the ground before you code

- **First run:** read the plan, `<TICKET>-plan-review.md`, `<TICKET>-analysis.md`, then the ticket.
- **Fix round:** read only the latest round of the report that sent you back (`<TICKET>-code-review.md`,
  `<TICKET>-verify.md` or `<TICKET>-pr-feedback.md`), the files it names, and the plan sections for those
  files. Change only what that report asks. From a pr-feedback report, fix only the items it classifies
  as to fix; leave questions, disagreements and out-of-scope items to the person who answers the PR.
  If an item changes an acceptance criterion, stop and report it: the plan needs a revision first.
- **Work in the checkout the user has open.** Never in a separate worktree or copy: the user reviews
  the uncommitted diff in their editor.
- **One implementation per checkout.** If the tree holds another ticket's uncommitted work, stop and
  ask.
- **Fix rounds build on your earlier edits.** Keep this ticket's edits from earlier rounds. Never stash,
  reset or check out over them.
- **Has the base moved?** Compare `git rev-parse <base>` with the SHA in the plan's §10. If it moved,
  list the files that changed (`git diff --name-only <plan SHA> <base>`). If none of them is a file the
  plan changes or depends on, continue and note it in the findings file. If any is, stop before editing
  and report `baseMoved` with that list: the plan needs a short re-review first.
- Re-run the reproductions the plan cites. Every disagreement with the plan goes into
  `<work>/<TICKET>-impl-findings.md` with the command, its output and the plan section it contradicts.
  Do not redesign in code to paper over it.

## 2. Implement

Make the §5 changes file by file and add the §6 tests. Check each profile invariant as you go.

## 3. Fast checks only

Run what the profile lists for implementation: lint, the unit tests related to the changed files, and
the new tests. Do **not** run the full unit suite or the integration suites; `/verify-ticket` runs the
full matrix once on the final tree. The one exception: an integration suite that the plan's §7 names as
the only proof of a criterion, run on its own on the stack the profile names for it. Never reset a
shared stack.

Record counts. A failure you cannot explain is a finding. If a known flaky test from the profile
fails, name it, re-run it once in isolation, and report both runs.

Give every file a Bash command writes (a redirect, `tee`, a heredoc target, a log) an absolute path,
never one relative to a `cd` earlier in the same command: the guard resolves a relative target
against the repository root and refuses it, even when the real target is a scratch file.

## 4. Leave it for a person

Leave the change as uncommitted edits in the working tree. No patch files. Never commit or push. Create a
branch only when your task is a pipeline branch step, with the name the profile gives ticket branches;
never otherwise. Prepare the plan's §12 commit and PR text, following the profile's git conventions.

A round that runs after a GREEN verification, for any reason (code review, PR feedback, a person's
request), makes that GREEN stale: the tree it verified is no longer the tree that will be committed. Say
so in your output, and name `/verify-ticket <TICKET>` as the required next step.

## Output

What changed, file by file; the fast checks with counts; whether the base moved; the findings file and
its open items; the prepared git texts. Next step: `/review-code <TICKET>` in a new session, or after a
verification fix round, `/verify-ticket <TICKET>`.
