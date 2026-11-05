---
name: pipeline-retro
description: After a ticket's pipeline run, or any time. Records the run (rounds, findings, escaped defects, time and tokens per phase, guard events) in a run log, turns repeated escapes into lesson candidates with evidence, and proposes skill or profile changes for a person to approve. `digest` mode summarises what is improving and what was delivered. Run it as /pipeline-retro <ticket>, or /pipeline-retro digest [days].
argument-hint: <ticket> | digest [days]
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-retro
# allowed-tools only pre-approves these tools for the turn that runs this skill.
# It restricts nothing. Use permission rules or disallowed-tools to restrict.
allowed-tools:
  - Read
  - Write
  - Edit
---

# Retro: learn from one run, and see the trend

The pipeline improves only from evidence. This skill measures; it never changes a skill or the profile.
It proposes exact edits, and a person decides.

## 0. Load the project profile

Read `.claude/pipeline.local.md` for the work directory and the ticket command. If it is missing, stop.
`<repo>` is the base name of the main checkout. Files this skill keeps:

- run log: `<work>/pipeline-runs.jsonl`, one JSON object per line, append only;
- lessons: `<work>/pipeline-lessons.md`;
- report: `<work>/<TICKET>-retro.md`.

They live in the work directory, next to the handoff files and out of git. Proposals target real files
only: the skills `~/.claude/skills/<name>/SKILL.md` (`plan-ticket`, `review-plan`, `implement-plan`,
`review-code`, `verify-ticket`, `pr-feedback`, `pipeline-retro`) and the profile `.claude/pipeline.local.md`.

## Input

`$ARGUMENTS`: a ticket (`<TICKET>`), or `digest` with an optional number of days (default 14). The
workflow may add the run's outcome; treat it as a pointer and check it against the files.

## Ticket mode

1. **Numbers you do not estimate.** Run `/usr/bin/python3 ~/.claude/pipeline/runstats.py <TICKET>` from the
   repository. It reads the transcripts and prints, per pipeline agent: skill, minutes, turns, first and
   peak context, token sums, and the guard events those agents hit. Use its numbers as they are.
   A workflow run keeps its agents under `<session>/subagents/workflows/<wf id>/`, which runstats does
   not search, and there an agent's first user message is the relayed user request, which may not name
   the ticket. If runstats lists fewer agents than the run had, apply its `agent_row` to that directory
   unchanged, with the ticket filter over the first two user messages, label each row from its
   `meta.json` description, and say so in the report.
2. **What happened**, from the handoff files only (`<TICKET>-plan-review.md`, `-code-review.md`,
   `-verify.md`, `-pr-feedback.md`, `-impl-findings.md`): plan-review rounds and each round's `Counts:`
   line, whether gate 1 closed or the plan was accepted by hand, code-review rounds and verdicts, verify
   runs and verdicts, PR feedback rounds. If the PR is known (plan §12, the feedback file, or
   `gh pr list --search "<TICKET>" --state all` read only), its state and merge date. Compare the tree of
   the last GREEN verify run (its HEAD and file list) with what was committed or merged; if anything
   changed after that run, record `verifiedTreeIsFinalTree: false` and say what changed.
3. **Escapes.** An escape is a defect found by a later phase that an earlier phase was responsible for:
   - a code-review finding about the plan's design or a missing criterion (plan review missed it);
   - a verify failure in code that code review closed (code review or the plan's §6 tests missed it);
   - a PR or CI item classified FIX or CI REAL (everything before it missed it).
   For each: where it was found, which phase should have caught it, and the check that would have caught
   it, in one sentence, concrete enough to become a rule. A known flaky test or an environment problem is
   not an escape; list it separately. Check every fact a lesson rests on against the current tree, not
   only the handoff files: something this ticket itself changed is history, not a standing gap.
4. **Append one line to the run log**:
   `{"ticket", "repo", "recordedAt", "mode", "outcome", "planRounds", "gate1", "planCounts": [...],
   "codeReviewRounds", "verifyRuns", "prFeedbackRounds", "pr": {"number", "state", "mergedAt"},
   "verifiedTreeIsFinalTree",
   "phases": [<runstats rows, without agentId>], "totals": {...}, "guardEvents": <count>,
   "escapes": [{"foundBy", "item", "missedBy", "missedCheck"}], "notEscapes": [...], "lessons": [<ids>]}`.
   If a line for this ticket already exists, append a new one with the newer `recordedAt`; never rewrite
   earlier lines.
5. **Lessons.** For each escape, look for a lesson in the lessons file that names the same missed check.
   - Same check: add this ticket and the item to its evidence.
   - None: add a lesson `L<n>` with status `candidate`: the missed check, the rule that would catch it,
     the target (`profile: <section>` if it is about this repository, `skill: <name>, <step>` if it holds
     for any repository), the evidence, and `retire_when` (the change that would make the rule pointless).
   - A lesson with evidence from two or more tickets becomes `proposed`: write the exact edit under it:
     the file, the text to replace quoted exactly as it stands now (read the file; the edit must apply),
     and the new text. If its `retire_when` already holds, say so instead of proposing.
   - A lesson `adopted` for ten or more runs whose check never appears in an escape or a finding since:
     propose retiring it, so the skills do not only grow.
   Never edit a skill, an agent definition or the profile. A person adopts a proposal and marks it
   `adopted` with the date.
6. **Report** to `<work>/<TICKET>-retro.md`: the numbers, the escapes, the lesson changes, and the
   proposed edits waiting for a decision.

## Digest mode

Read the run log entries of the last N days and the lessons file. Write a short digest in your reply (not
to a file): per ticket, rounds to gate 1, escapes, minutes and tokens, outcome and PR state; the trend
against the previous N days for rounds, escapes, minutes and tokens per ticket; delivered (merged PRs,
plan to merge time); lessons by status, and the proposals waiting for a decision. Say how many runs the
numbers rest on; with fewer than three, say that no trend can be read yet.

## Rules

- Measure, do not estimate: numbers come from runstats or the files, or are marked as missing.
- GitHub and the ticket system are read only. Never commit, branch or push.
- Never edit a file in place (`sed -i`, `perl -i`), not even a scratch script: the guard refuses it for this role whatever the path. Write a changed scratch file whole, with `Write` or a heredoc to an absolute path.
- No internal ids in anything meant for GitHub; this skill writes nothing meant for GitHub.
- Give every file a Bash command writes (a redirect, `tee`, a heredoc target, a log) an absolute path, never one relative to a `cd` earlier in the same command: the guard resolves a relative target against the repository root and refuses it, even when the real target is a scratch file.

## Output

Ticket mode: the outcome, escapes (count and one line each), lesson changes, and proposals waiting for
a decision. Digest mode: the digest.
