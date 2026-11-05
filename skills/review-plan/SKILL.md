---
name: review-plan
description: Phase 2 of 5 of the ticket pipeline. Adversarially reviews another agent's plan for one ticket, records findings, and gives a readiness verdict. Reports only; the planner applies the findings. Run it as /review-plan <ticket>, one round per fresh session.
argument-hint: <ticket> [artifact URLs] [file paths]
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-plan-reviewer-artifacts
# allowed-tools only pre-approves these tools for the turn that runs this skill.
# It restricts nothing. Use permission rules or disallowed-tools to restrict.
# Edit is here only to append to the review file.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Edit
  - WebFetch
---

# Phase 2 of 5: review the plan

Pipeline: `/plan-ticket` → `/review-plan` (this) → `/implement-plan` → `/review-code` → `/verify-ticket`.
You did not write this plan, and a plan that reads well is not evidence that it is correct.
Each invocation is one round, and runs as a fresh agent (by hand through `context: fork`, in the
workflow as a new agent), so the previous round's reasoning does not steer this one.

You find; you do not fix. The planner applies your findings in its own step (`/plan-ticket <TICKET>
revise`), in a fresh session, and the next review round checks what it did. Do not edit the plan or
the analysis.

## 0. Load the project profile

Read `.claude/pipeline.local.md`. If it is missing, stop and ask the user to create it.
`<work>` is the profile's work directory.

## Input

`$ARGUMENTS`: the ticket (`<TICKET>`, required), optional artifact URLs and file paths.
If `<work>/<TICKET>-plan.md` does not exist, stop: run `/plan-ticket` first.

## What to do, in this order

1. **Ticket and code first.** Read the ticket, its parent epic and the code the change touches.
   Before you open the plan, write down under this round's "Expectations" what a correct change
   must do and where it is most likely to break. This is your own baseline, not the planner's.
2. **Then the plan. Read the planner's analysis, and the planner's responses to earlier rounds, last.**
3. **Verify every load-bearing claim** by reading and by running, not by re-reading the plan's own
   evidence. For each acceptance criterion: does the §5 design meet it, and is there a §6 test that
   would fail without the change? Re-run the plan's targeted checks where you can; do not run the
   full test suites, which verification runs once at the end. Trust what you run.
   A criterion that states several properties (an order, a call from the entry point, a count, a
   bound) needs, for each property, an assertion that fails when only that property is broken. A
   negative control against the base proves only that the new code exists: apply the one-line
   mutant for each property to the plan's test block in a scratch copy and see a test fail. A
   property no assertion catches is a finding, not an observation, even when the code around it
   was untested before the change.
4. **Hunt for defects the plan does not mention**:
   - each profile invariant;
   - races, ordering, retries, partial failures, error paths, boundary values;
   - acceptance criteria with no test, or a design that meets one only partly;
   - interface drift between the change and its callers or clients;
   - scope drift beyond the ticket;
   - files the checks do not see (untracked new files), sibling tickets that merged.
   Prototype only in a scratch copy, on the stack the profile names for reads; never reset a shared stack.
5. **Check the base, as information.** Compare `git rev-parse <base>` with the SHA in the plan's §10 and
   list the files that changed since (`git diff --name-only <plan SHA> <base>`). Record it under the
   round's "Base" line: the current SHA and the changed files the plan touches or depends on. A moving
   base is not a finding by itself; `/implement-plan` re-checks it before editing. Raise a numbered
   finding only when a change on the base breaks something the plan relies on, and say what.
6. **Append the round** to `<work>/<TICKET>-plan-review.md` under `## Round N — <date>`:
   - "Expectations" from step 1;
   - "Base" from step 5;
   - every earlier finding re-checked against the current plan: resolved, still open, or regressed.
     A finding the planner rejected with evidence counts as resolved if the evidence holds; say so.
     If it does not hold, it stays open, with the reason;
   - new findings numbered on from the last (`F1`, `F2`, …). Each has severity
     (Critical/High/Medium/Low), location, the defect in one sentence, a concrete failure
     scenario, the evidence you ran or read, a verdict (CONFIRMED, PLAUSIBLE or REJECTED, with the
     reason for a rejection) and the exact plan change it needs;
   - the count line, exactly in this form, counting CONFIRMED and PLAUSIBLE findings:
     `Counts: new C/H/M/L <n>/<n>/<n>/<n>; open C/H/M/L <n>/<n>/<n>/<n>`
   - the verdict line: `Verdict: READY` or `Verdict: NOT READY`.

## Verdict

Count only CONFIRMED and PLAUSIBLE findings.

- **READY**: this round raised no new Critical or High finding, and no Critical or High finding is
  open. Open Medium and Low findings are allowed: the planner folds them or records a decision for each.
- **NOT READY**: anything else.
- A regressed finding counts as new in this round.

## Rules

- `<work>` is gitignored. Never commit, branch or push.
- Write only the review file. Do not edit the plan, the analysis, or any code.
- Never edit a file in place (`sed -i`, `perl -i`, `prettier --write`, `eslint --fix`), not even a scratch copy: the guard refuses it for this role whatever the path. Write a changed scratch file whole, with `Write` or a heredoc to an absolute path.
- Never post to the ticket system unless the user asks in this conversation. Findings live in the
  review file, not in code comments.
- Report a disproved claim as REJECTED with the evidence; do not drop it quietly.
- Give every file a Bash command writes (a redirect, `tee`, a heredoc target, a log) an absolute path, never one relative to a `cd` earlier in the same command: the guard resolves a relative target against the repository root and refuses it, even when the real target is a scratch file.

## Output

The verdict; new findings this round and open findings after it, each by severity; whether the base
moved and which planned files it touched; and the next step: `/plan-ticket <TICKET> revise` in a new
session (when anything is open), then another `/review-plan <TICKET>` if the verdict was NOT READY,
or `/implement-plan <TICKET>` if it was READY.
