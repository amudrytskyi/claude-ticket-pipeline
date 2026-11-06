# Ticket pipeline for Claude Code

Nine skills, a set of agent definitions, two hooks and one workflow that take a ticket from issue to
reviewed, verified, uncommitted code, turn pull-request feedback into a fix round, and learn from each run.
Phases hand off through files on disk, so each phase can run in a fresh session with clean context.

```
/plan-ticket → /review-plan ⇄ /plan-ticket revise (until a round raises no Critical or High)
             → /implement-plan ⇄ /review-code
             → /verify-ticket (full test matrix once; on failure a fix round, then again)
             → /plan-ticket git-texts (commit message + PR description, Testing from verify) → you commit
after the PR: /pr-feedback → /implement-plan (fix round) → /review-code → /verify-ticket
after every run: /pipeline-retro (run log, escaped defects, lesson candidates; proposes, never edits)
every week:     /repo-pulse (what everyone merged → "how the project works now", conventions, stale notes)
```

## What is here

| Path | What it is |
|---|---|
| `skills/plan-ticket/SKILL.md` | Phase 1. Analysis and a plan another agent can build from; records the base SHA. `revise` mode applies a review round; `git-texts` mode writes the final commit message and PR description. |
| `skills/review-plan/SKILL.md` | Phase 2. Adversarial review of the plan, one round per fresh session. Reports only. |
| `skills/implement-plan/SKILL.md` | Phase 3. Implements from the plan in your open checkout with fast checks only; also runs fix rounds. |
| `skills/review-code/SKILL.md` | Phase 4. Closes out every acceptance criterion against the diff; runs no test suites. |
| `skills/verify-ticket/SKILL.md` | Phase 5. Runs the full test matrix once on the final tree. |
| `skills/pr-feedback/SKILL.md` | After the PR. Reads review comments and CI read only, classifies each against the code, prepares replies. |
| `skills/ticket-overlap/SKILL.md` | Before a batch. Reports per pair of tickets: independent, shared files, dependent or same work, and which ticket builds on which. |
| `skills/repo-pulse/SKILL.md` | Periodic. Reads what everyone merged since the last pulse, keeps `repo-knowledge.md`, learns review conventions, proposes fixes for stale notes. |
| `skills/pipeline-retro/SKILL.md` | After a run. Appends it to a run log, finds escaped defects, keeps lesson candidates, proposes edits. `digest` mode shows the trend. |
| `agents/pipeline-*.md` | One agent type per role, each with a tool allowlist; the `-artifacts` variants add the Artifact tool. The workflow runs each phase as one of them; the guard keys on the type. |
| `pipeline/guard.py` | PreToolUse hook. Acts only on `pipeline-*` agents; everything else passes. |
| `pipeline/report_check.py` | SubagentStop hook. A report agent cannot finish without its report's latest section in the required form; blocks once. |
| `pipeline/prscan.py` | Triage of the PRs merged since the last pulse, from git and one read-only gh call: high, medium, low, and the notes a merge may have made stale. |
| `pipeline/run-repo-pulse.sh`, `pipeline/com.claude.repo-pulse.plist.example` | Headless weekly run of the pulse under the guard, and a launchd job for it (not installed by anything here). |
| `pipeline/runstats.py` | Per-phase minutes, turns, context and tokens for one ticket, read from the transcripts. Used by the retro. |
| `pipeline/settings.hooks.example.json` | The hook entry to merge into `~/.claude/settings.json`. |
| `workflows/ticket-pipeline.js` | Optional. Chains the phases with two gates and the repair loops. |
| `pipeline.local.example.md` | Template for the per-repository profile every phase reads first. |

## Install

1. Copy the nine skill folders to `~/.claude/skills/` (personal, every project) or to a
   repository's `.claude/skills/` (shared with the team).
2. Copy `agents/*.md` to `~/.claude/agents/`, the three scripts in `pipeline/` to `~/.claude/pipeline/`,
   and merge `pipeline/settings.hooks.example.json` into `~/.claude/settings.json` (merge the arrays; do
   not replace your other hooks). The guard needs Python 3.9 or later. New agent types load when a session starts.
3. In each repository, copy `pipeline.local.example.md` to `.claude/pipeline.local.md`, fill it in,
   and keep it and the work directory it names out of git.
4. Optional: copy `workflows/ticket-pipeline.js` to `~/.claude/workflows/` or `.claude/workflows/`.

## Run it by hand (works everywhere)

Open a new session for each step and pass the same ticket:

```
/plan-ticket 123 https://link-to-a-design-note
/review-plan 123          # new session per round
/plan-ticket 123 revise   # new session: apply that round; then /review-plan again until READY
/implement-plan 123
/review-code 123          # on GAPS: /implement-plan 123, then /review-code 123 again
/verify-ticket 123        # on RED: /implement-plan 123, then /verify-ticket 123 again
/plan-ticket 123 git-texts # commit message + PR description in <work>/123-git-texts.md
/pr-feedback 123 456      # after the PR has comments or red checks; then /implement-plan 123
/pipeline-retro 123       # after a run; /pipeline-retro digest 30 for the trend
```

The handoff files carry the context, so nothing is lost between sessions. Each skill declares
`context: fork` and its agent type, so a skill you run by hand runs as that pipeline agent in a fresh
context, under the guard and the report check, and returns a summary to your session. By hand the skills
use the `-artifacts` variants, so nothing a phase might read is out of reach.

## Run it as a workflow

Ask Claude Code to run the `ticket-pipeline` workflow with the ticket as its argument, for example
"run the ticket-pipeline workflow on #123". Options, passed as an object:
`{ input, mode, pr, skillDir, maxPlanRounds, maxFixRounds, maxVerifyFixes, verifyEffort,
pauseBeforeImplement, acceptPlan, agentTypes }`.
For PR feedback: `{ mode: "pr-feedback", input: "#123", pr: 456 }`.
Workflow availability depends on your plan and settings; see
[the workflows documentation](https://code.claude.com/docs/en/workflows.md).
If workflows are not available to you, the manual run above does the same work.

**Several tickets in one run.** Pass `{ mode: "multi", tickets: ["#123", "#124"] }`, in the order you want them
built. An overlap check comes first and stops the run if two tickets are the same work or the order
conflicts with a dependency. Then every ticket is planned and plan-reviewed in parallel; each planner reads
the overlap report, so the plans split shared code instead of doing it twice. Then the tickets are built one
at a time, in your order: a branch step creates `task/<ticket>-<slug>` (from the base, or from an earlier
ticket's branch when the overlap check stacks it), and the ticket goes through implement, code review and
verify as usual. When it is green the run stops. You read the diff and commit on that branch; then you resume
with `resumeFromRunId` and the `resumeArgs` the stop returned (`go` moves to the next ticket; finished work
comes from cache). A stacked ticket whose plan the earlier commit made stale gets one plan-review round and a
revision before it is built. If the branch step finds uncommitted work it stops, and the `resumeArgs` it
returns carry a new attempt number, so the retry is not answered from cache. The guard lets only the
implementer create a branch, only with the profile's `branchPattern`, and only from a clean tree; commits
stay with you.

**Plan several tickets at once, by hand.** Planning and plan review do not touch your checkout. Start one run per
ticket with `pauseBeforeImplement: true`; each stops after its plan passes gate 1. Then resume them one at
a time with `resumeFromRunId` and `pauseBeforeImplement: false`. The finished phases come from cache.

**When gate 1 does not close.** After `maxPlanRounds` the run stops and logs the findings per round. If the
counts are not falling, more rounds will not help: read the review file and decide. To go on with the plan
as it is, resume with `resumeFromRunId` and `acceptPlan: true`.

## Context per agent

Every turn of an agent re-reads its whole context, so what an agent carries is paid for on every turn.
Four things keep that down without changing what any phase reads, knows or decides:

- **Tool allowlist.** Each agent type lists the tools pipeline phases have actually used and nothing
  else. Tool definitions are part of every turn; in our measurement an agent started at about 42k tokens
  with every tool and at about 20k with the allowlist.
- **Artifact tool only where it can be needed.** Its definition alone is about 15k tokens per turn. The
  first planner always has it and reports every artifact link it found in the input, the ticket, its epic
  and their notes; later phases get the `-artifacts` variant when the input or the planner names one. A
  phase without it that meets a link stops before doing anything and returns `needsArtifactTool`, and the
  workflow restarts that step with the variant.
- **Independent read-only calls in one turn.** File reads, searches, `git log`/`diff`/`show` that do not
  depend on each other go out together; test runs, builds and edits stay one at a time.
- **Pointers in fix-round prompts.** The workflow passes the previous step's failing tests, open finding
  ids and items to fix into the prompt. The agent still reads the report the skill names.

## Tests by phase

| Phase | Runs |
|---|---|
| Plan, review plan | targeted checks and prototypes in a scratch copy, on the stack the profile names for reads |
| Implement | lint, unit tests related to the changed files, the new tests; one integration suite only when the plan names it as the only proof of a criterion |
| Review code | no suites; a single test only to decide a criterion |
| Verify | the full matrix, once, on the final tree, each step on the stack the profile names for it |

Running the expensive suites once at the end, instead of in every implement and review round, is the
main time saving. The cost: an integration failure shows up late and costs one fix round.

## Two gates

- **Before any code (automated):** the latest plan-review round raised no new Critical or High finding
  and left none open. The reviewer only reports; a fresh planner agent applies each round, and the next
  round checks what it did, so no agent certifies its own edit. A moving base is recorded as information
  for the implementer, not raised as a plan finding: on an active base branch it would come back every
  round and the gate would never close.
- **Before any commit (human):** nothing in this pipeline commits, branches or pushes. You read the
  diff and commit. Fix rounds after a failed verification skip code review, so read those changes too.

## The guard

`pipeline/guard.py` runs before every Bash, Write, Edit and NotebookEdit call and acts only when the call
comes from a `pipeline-*` agent. It refuses, with a reason the agent reads:

- git that commits, pushes, stashes, branches, merges, rebases or discards working-tree edits;
- any write to GitHub (`gh pr`/`gh issue` writes, `gh api` or `curl` with a write method);
- stopping, removing, restarting or writing SQL into the containers the profile protects, and Docker prunes;
- a command the profile lists as destructive (one that resets its database) when it points at a shared
  stack's port or names no target at all;
- file writes outside the role: reviewers, the verifier and pr-feedback write only their own report; the
  planner and implementer write only inside the repository, never under `.git/` or `.claude/`.

Repository specifics live in the profile's `## Guard` block. Each refusal is logged to
`~/.claude/pipeline/guard.log`. Shell commands are tokenized, so quoted text such as a SQL `>` or a
`grep 'git push'` is not mistaken for a redirect or a push; a command that cannot be tokenized is checked
more strictly. The guard is a guardrail for agents that follow instructions, not a sandbox: a determined
process can still write files in ways it does not parse.

## Learning from runs

The retro runs at the end of every workflow run, finished or stopped, and by hand with `/pipeline-retro`.
It appends one line per run to `pipeline-runs.jsonl` in the work directory: rounds, the counts per plan-review round,
verdicts, minutes and tokens per phase from `runstats.py`, guard events, and escapes. An escape is a
defect a later phase found that an earlier phase was responsible for; for each, the retro names the check
that would have caught it. Repeated missed checks become lessons in `pipeline-lessons.md` in the work directory,
with their evidence and a `retire_when`. Both files sit next to the handoff files, out of git; files under
`~/.claude` are treated as sensitive, so an agent cannot write them without asking. Every fact a lesson
rests on is checked against the current tree, and a proposed edit quotes the text it replaces as it
stands, so it applies. A lesson seen on two tickets gets an exact proposed edit for the
profile or a skill. The retro never applies it: you do, and mark it adopted. Lessons that stop earning
their place are proposed for retirement, so the skills do not only grow. `/pipeline-retro digest` shows
the trend and what was delivered, and says when there are too few runs to read one.

## Keeping up with everyone else's work

The retro learns from your own runs. `/repo-pulse` learns from everything merged into the base branch,
by anyone. `prscan.py` sorts the merged PRs without spending tokens: a PR is high when it changes the
instruction files, the runtime version, build or lint config, adds a migration or a guard test, removes a
module, moves a dependency's major version or a script people run, or deletes a path a memory note names.
The pulse agent reads only those in full, skims the medium ones, and counts the rest. It keeps
`repo-knowledge.md` in the work directory: a short `## Now` (what every session should know, each fact with
its PR or file), the team's conventions learned from review comments (a request seen on two PRs or from two
reviewers), and a changelog per pulse. The profile tells every phase to read `## Now` before relying on the runtime, a command or the stack; the
skills themselves do not name it. Stale memory
notes, profile lines and lessons come back as exact proposed edits; you apply them.

To run it weekly, copy `pipeline/com.claude.repo-pulse.plist.example` to `~/Library/LaunchAgents/`, fill in
the two paths, and load it with `launchctl bootstrap gui/$(id -u) <plist>`. The whole headless session runs
under the guard as `pipeline-learner` (`PIPELINE_GUARD_ROLE`), so it cannot commit, push, post or write
anything but its own three files. A cloud schedule cannot do this job: it cannot see your local memory,
profile or work directory.

## The report check

`pipeline/report_check.py` runs when a subagent stops. For the plan reviewer, code reviewer, verifier,
pr-feedback and retro agents it checks that the agent wrote its report in this run, and that the report's
latest section has what the skill requires (for example the plan review's
`Counts: new C/H/M/L …; open C/H/M/L …` and `Verdict:` lines). If not, it blocks the stop once and tells the
agent what is missing; an agent that stopped on purpose says so and finishes. It finds the report in the
profile's work directory, prefers the ticket the agent's task names, and logs each block to the guard log.

## Things to know

- `allowed-tools` in a skill's frontmatter pre-approves tools for the turn that runs the skill. It
  does not restrict anything. Use permission rules or `disallowed-tools` to restrict
  ([skills documentation](https://code.claude.com/docs/en/skills.md)).
- In the workflow, each agent reads the skill file as text, so the skill's frontmatter does not apply to
  it. The agents run under your session's permission rules, their agent type's tool list, and the guard.
- The workflow trusts the verdicts and counts its agents report. A workflow script cannot read files,
  so there is no deterministic check; each check runs in its own agent, separate from the one it checks.
- Implementations share your open checkout. Run one implementation per checkout at a time, and do not
  commit while a code review or verification is running: they read the working tree.
- The plan records the base SHA. If the base moves and touches files the plan depends on, implementation
  stops before editing and asks for a short re-review instead of building on a stale plan.
- Do not let agents reset a stack you or other sessions use. Suites that drop their database need a
  throwaway stack the phase starts and removes; write that recipe into the profile, including how to point
  every step at it. Leaving the target unset is not isolation: defaults usually point at your local stack.
- `git diff` does not show untracked files. Code review and verification list them explicitly, because
  new tests and migrations are usually untracked until you commit.
- `/pr-feedback` treats a reviewer's comment as a claim to check, not an order. Replies are prepared in the
  feedback file; you post them.

## Known limits

- The workflow trusts the verdicts and counts its agents report. A workflow script cannot read files, so no step re-checks a report; keeping agents separate is the check.
- When gate 1 closes with Medium or Low findings open, the planner's last fix is not re-reviewed. The person sees it at gate 2.
- Fix rounds after a RED verification skip code review.
- Implementations share the open checkout and its branch: one ticket at a time, and no commits while a review or a verification is reading the tree.
- Resuming a stopped run returns the cached result of every step whose prompt did not change, so resuming after BLOCKED or base-moved replays the stop unless something in the call changes.
- In batch mode the refresh round does not move the plan's base SHA, so a stacked ticket can stop at base-moved again.
- The guard is a guardrail for agents that follow instructions, not a sandbox:
  - it checks the scripts inside `bash -c` and `eval`, except when the quoted script contains a redirect;
  - it checks writes made with Write, Edit, redirects, `tee` and the common in-place editors, but not `rm`, `cp`, `mv`, `sed --in-place` or scripts that write files;
  - a hook that cannot start, times out (10 s) or exits with an error lets the call through;
  - Agent and Monitor are in every agent's tool list, and a subagent that a pipeline agent starts is guarded only if it is itself a `pipeline-*` type.
- `report_check.py` takes the ticket number from the request relayed to the workflow. When that names several tickets or none, it checks the newest fresh report, which can belong to another ticket.
- `runstats.py` does not see agents that a workflow started; the retro measures them by hand.
- New agent types and skills load when a session starts.

## Read more

- Part 1: [Four phases, two gates: what my logs say about an AI ticket pipeline](https://www.linkedin.com/pulse/four-phases-two-gates-what-my-logs-say-ai-ticket-artur-mudrytskyi-1e6of/)
- Part 2: [Half the cost, three tickets a day. It still needs me four times per ticket](https://www.linkedin.com/pulse/half-cost-three-tickets-day-still-needs-me-four-times-mudrytskyi-82i5f/)
- [Reference page](docs/index.html): the design, the measured numbers and every file in one place
