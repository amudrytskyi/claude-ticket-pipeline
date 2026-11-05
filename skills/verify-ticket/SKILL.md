---
name: verify-ticket
description: Phase 5 of 5 of the ticket pipeline. Runs the full test matrix once on the final working tree, on the stacks the profile names, and reports GREEN, RED with the failing tests, or BLOCKED. Changes no code. Run it as /verify-ticket <ticket>.
argument-hint: <ticket>
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-verifier
# allowed-tools only pre-approves these tools for the turn that runs this skill.
# It restricts nothing. Use permission rules or disallowed-tools to restrict.
# Test commands are not listed here: allow them in your permission settings.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
---

# Phase 5 of 5: verify

Pipeline: `/plan-ticket` → `/review-plan` → `/implement-plan` → `/review-code` → `/verify-ticket` (this).
Implementation and code review ran fast checks only. This phase runs everything, once, on the tree a
person is about to commit. You did not write the code, and you do not change it.

## 0. Load the project profile

Read `.claude/pipeline.local.md`. Use its runtime, its full test matrix for the Verify phase, its test
stack and its known flaky tests. If it is missing, stop and ask the user to create it.

Then check the base, read only, before any step runs. If the profile names a base-health check, find
the merge base of HEAD with the base branch the plan's §10 names (or the profile's default) and run the
check. Record any job that is already failing on the base: those are **known base failures**, and a
failure in the matching step is a candidate for the `base` class in §3.

## 1. Record what you are verifying

Record `git rev-parse HEAD`, `git status --porcelain` and the untracked files
(`git ls-files --others --exclude-standard`). Verification covers exactly this tree. If another
ticket's uncommitted work is in it, stop and report BLOCKED.

If an earlier run in the verify file was GREEN, say whether this tree differs from the one it recorded.
A GREEN describes only the tree it ran on; after any change since, this run is the one that counts.

## 2. Check the stack

The profile says which stack each step runs on. A shared stack: check it with the profile's command, and
if it is down, start it the way the profile says; never reset, restart or reconfigure it. A throwaway
stack the profile prescribes (for steps that reset their database): start it as the profile says, use it
for those steps only, and remove it before you finish, also when a step fails. Run the profile's leak
check on those steps' logs and report what it found. If a stack cannot be brought up, report BLOCKED with
what failed.

## 3. Run the full matrix

Run every step of the profile's Verify matrix, in its order, with the profile's runtime. A step the
matrix marks as conditional runs when its condition holds; say which way you decided and why. Run
independent steps side by side only when they do not share a database or ports. For each step record the command,
the exit code, the counts (passed, failed, skipped) and the time it took.

- A failure in a known flaky test: re-run that test once in isolation and report both runs. If it passes
  in isolation, it does not make the run RED, but it is named.
- Any other failure: do not re-run it until it passes. Record the test, its file, and the first lines of
  the error.
- A step that cannot run (missing dependency, service down): record it as a blocker. Never report a step
  you did not run as passed.

If the profile says how to keep the host awake (for example a wrapper command), run the long steps with
it. Record each step's start and end time.

**Classify every failure that is not a known flaky test**, in this order. When the evidence is not
clear, use `change`.

1. **environment**: the step ran while the host slept (the profile's sleep check shows a sleep inside the
   step's window), or the error matches one of the profile's environment signatures. Re-run only the
   failing tests once (the whole step if they cannot be run alone), awake and with nothing else running.
   - Passes: the failure does not make the run RED. Name it with the reason.
   - Fails again the same way: the environment is broken. The verdict is BLOCKED, with what needs
     fixing.
   - Fails differently: classify the new failure from the top.
2. **base**: the failing test, and the code it exercises, lie outside the files this change touches.
   The failure is in a known base failure, or it reproduces on the merge base. To check, run only that
   test in a detached worktree of the merge base, on its own throwaway stack if it needs one (the
   profile says how). Remove the worktree before you finish. A fix round on this ticket cannot fix a
   base failure, so it is reported apart from the others.
3. **change**: everything else. These are what a fix round fixes.

Before you finish, check `git rev-parse HEAD` and `git status --porcelain` again. If the tree changed
during the run, say so: the results describe the tree you recorded, not the new one.

## 4. Write the report

Append to `<work>/<TICKET>-verify.md` under `## Run N — <date>`: the HEAD and the tree you verified, the
stacks each step ran on (and that the throwaway one was removed), a table of steps (command, exit code,
counts, minutes), the base check and its known base failures, and for each failure the test, its file,
the first lines of the error, its class and the evidence for the class (the sleep window, the matching
signature, or the merge-base run and its result), and end with
the verdict line: `Verdict: GREEN`, `Verdict: RED` or `Verdict: BLOCKED`. Change no code and no plan.

## Verdict

- **GREEN**: every step ran and passed. Allowed, and named: a known flaky test that passed in isolation,
  and an environment failure that passed on its re-run.
- **RED**: at least one `change` or `base` failure. List the `change` failures with their files, so a
  fix round can re-run just those, and list the `base` failures separately.
  - Next step with `change` failures: `/implement-plan <TICKET>` (fix round), then `/verify-ticket
    <TICKET>` again.
  - With only `base` failures there is no fix round. The person updates the branch from the base, or
    waits for the base to be fixed.
- **BLOCKED**: a step could not run, or an environment failure came back on its re-run. Say what is
  needed.

On GREEN the pipeline is done: a person reads the diff and commits.
