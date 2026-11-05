---
name: plan-ticket
description: Phase 1 of 5 of the ticket pipeline. Turns one ticket into an implementation plan that a different agent can build from without this conversation, and revises it after each plan-review round. Run it as /plan-ticket <ticket>, or /plan-ticket <ticket> revise after a review round.
argument-hint: <ticket> [revise | git-texts] [artifact URLs] [file paths]
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-planner-artifacts
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

# Phase 1 of 5: write the plan

Pipeline: `/plan-ticket` (this) → `/review-plan` → `/implement-plan` → `/review-code` → `/verify-ticket`.
The phases hand off through files on disk, never through chat. Your job is a plan that another
agent can implement from the file alone.

## 0. Load the project profile

Read `.claude/pipeline.local.md`. It names the ticket command, the work directory for handoff
files, the base branch, the gate commands, the invariants, the docs obligations and the known
flaky tests. If it is missing, stop and ask the user to create it from `pipeline.local.example.md`.
Below, `<work>` is the profile's work directory.

## Input

`$ARGUMENTS`:

- **Ticket** (required): an issue URL or id. Everything downstream keys off it; call it `<TICKET>`.
- **`revise`** (optional): apply the latest plan-review round instead of writing a new plan. See
  "Revise mode" below.
- **`git-texts`** (optional): write the final commit message and PR description for the change in the
  open checkout. See "Git-texts mode" below.
- **Artifact URLs** (optional): fetch and read each one.
- **File paths** (optional): read each one.

If no ticket can be resolved, stop and ask. Never invent a ticket id.

## What to do

1. **Gather the full picture.** Read the ticket and its parent epic with the profile's ticket
   command, every artifact and file passed in, and any design notes the ticket links. Read the code
   the change touches and follow the real control flow, not the ticket's description of it.

2. **Write the analysis** to `<work>/<TICKET>-analysis.md`:
   - what the change must do, and the complete surface it touches;
   - each profile invariant, checked against that surface;
   - races, ordering, partial failures, retries and edge cases;
   - adjacent defects you notice that are out of scope. List them; do not fold them in silently.

3. **Write the plan** to `<work>/<TICKET>-plan.md`. This file is the specification. Make every
   claim one you established by reading or running, and say which.
   - **§1 Status**: is it ready to implement, and what still blocks it.
   - **§2 Acceptance criteria**: the ticket's own, plus any you drafted. Mark drafted ones
     "prepared, not posted" and give the reason each exists. State that §2 is the spec and the
     ticket body is the subset the owner has seen.
   - **§3 Evidence**: how each load-bearing claim was proven (test, command, result).
   - **§4 Design**: the mechanism of the change.
   - **§5 Per-file changes**: file by file, what changes and why.
   - **§6 Tests**: unit and integration tests to add or change. For each criterion, the assertion
     that fails without the change; for a criterion that states several properties (an order, a
     call from the entry point, a bound), one assertion per property, each run once against a
     mutant that breaks only that property.
   - **§7 Tests by phase**, from the profile: the fast checks implementation runs (lint, unit tests
     related to the changed files, the new tests), and the full matrix verification runs once at the
     end. Name an integration suite for implementation only when it is the only proof of a criterion.
   - **§8 Risks**.
   - **§9 Decisions and open questions**, with your recommendation for each.
   - **§10 Base**: the base branch and its commit SHA when you planned (`git rev-parse <base>`), and
     how to rebase if the base moves. Later phases compare against this SHA.
   - **§11 Ticket text**: prepared wording for the ticket, not posted.
   - **§12 Git texts**: commit subject and PR description, prepared only.

4. **Verify by running** where you can. If a run disagrees with the ticket, an artifact or your own
   earlier reasoning, trust the run, write it down, and change the plan. Prototype in a scratch copy
   (for example a temporary worktree), never in the open checkout, so several tickets can be planned at
   once. Prototypes that need services use the stack the profile names for them; never reset a shared
   stack.

## Revise mode

Run when the arguments say `revise`, in a fresh session, after a `/review-plan` round. The reviewer
reports; you decide and edit. You are not the agent that wrote the plan, so read it as a stranger would.

1. Read the latest `## Round N` of `<work>/<TICKET>-plan-review.md`, then the plan sections and the code
   each open finding names. Read only what those findings need.
2. Decide every open finding, new or carried over:
   - **ACCEPTED**: fold the change into the plan, so the plan stays the single source of truth. A
     finding that changes a criterion changes §2 (marked "prepared, not posted") and its §6 test.
   - **REJECTED**: only with evidence you ran or read that disproves it. Record it in §9.
   - **DEFERRED**: Medium or Low only, with the reason and who decides. Record it in §9.
   Critical and High findings are accepted or rejected, never deferred.
3. Do not rewrite sections no finding touches. Do not answer a "Base" note by moving §10: the base SHA
   moves only when a finding needs it, and then §10 says why.
4. Append `### Planner response` under that round in the review file: one line per finding (id,
   ACCEPTED, REJECTED or DEFERRED, the plan sections changed, or the evidence for a rejection), then
   the list of sections changed in this revision.

Revise mode output: counts of accepted, rejected and deferred findings, the sections changed, and the
next step: another `/review-plan <TICKET>` in a new session.

## Git-texts mode

Run when the arguments say `git-texts`, after implementation, code review and verification (or after
whichever of them ran). You write the texts a person will paste when they commit and open the PR; you
change no code and no plan.

1. Read the plan's §12, the latest run in `<work>/<TICKET>-verify.md` (if any), the latest round in
   `<work>/<TICKET>-code-review.md` (if any), and the change itself: `git status --porcelain`,
   `git diff HEAD --stat` and the diff where §12's Changes list needs checking.
2. Write `<work>/<TICKET>-git-texts.md`, replacing any earlier copy, with exactly these sections:
   `## Commit message` (subject line, then the body if §12 has one, in one fenced block),
   `## PR title`, and `## PR description` (in one fenced `markdown` block, ready to paste).
3. Start from §12. Make the Changes list match the diff (files added, removed or formatted since the
   plan). Fill every placeholder in Testing from the verify report: each step's command and counts as
   the report gives them. A step that did not run, failed or hung is stated as such with its reason and
   any ticket filed for it; never write a step as passed that the report does not show as passed. If no
   verify run exists, say in Testing that the full matrix has not run yet.
4. In PR-feedback mode (the task says so), write only the commit message for the fix round under
   `## Commit message`, and under `## PR description` either the sections of the existing PR body
   that this round makes untrue, rewritten, or the line `No change needed.`
5. Follow the profile's git-text conventions and §12's style. No placeholders may remain.

Git-texts mode output: the file path, the commit message, the PR title and the PR description, as
written.

## Rules

- `<work>` is gitignored. Never commit, branch or push. Prepare git texts only.
- The plan is the deliverable. Do not produce patch files.
- Give every file a Bash command writes (a redirect, `tee`, a heredoc target, a log) an absolute path, never one relative to a `cd` earlier in the same command: the guard resolves a relative target against the repository root and refuses it, even when the real target is a scratch file.
- Never post to the ticket system unless the user asks in this conversation.
- The independent check is `/review-plan`, which runs as its own fresh agent.

## Output

The paths you wrote, a one-line readiness verdict, and the next step: run `/review-plan <TICKET>`
in a new session. (Revise mode and git-texts mode have their own output, above.)
