# Pipeline profile

Copy this file to `.claude/pipeline.local.md` in your repository and keep it out of git
(`.gitignore`, or `.git/info/exclude` if you do not want to touch tracked files).
Every phase skill reads it first, and the guard hook reads its `## Guard` block. It holds everything that
is specific to one repository, so the skills, agents and guard stay generic and can live in `~/.claude/`.

Before you rely on the runtime, a command or the stack below, read `## Now` in `<work>/repo-knowledge.md`,
which `/repo-pulse` keeps current. Keep this line: the skills themselves do not name that file.

## Ticket system

- Read a ticket: `gh issue view {ticket} --repo <owner>/<repo>`
- Find the parent epic: <for example, "the `Epic:` link in the issue body">
- Design notes: <where they live and how to read them>
- PR feedback (for `/pr-feedback`), read only: `gh pr view {n} --json title,body,state,headRefName,baseRefName,reviewDecision,reviews,comments,statusCheckRollup`;
  inline comments: `gh api repos/<owner>/<repo>/pulls/{n}/comments --paginate`; checks: `gh pr checks {n}`;
  a failing job's log: `gh run view <run id> --log-failed`

## Work directory

- Handoff files go in: `.agent-work/` (kept out of git). The report check reads this line: keep the path
  in backticks, ending with `/`.
- File names: `{ticket}-analysis.md`, `{ticket}-plan.md`, `{ticket}-plan-review.md`,
  `{ticket}-impl-findings.md`, `{ticket}-code-review.md`, `{ticket}-verify.md`, `{ticket}-pr-feedback.md`, `{ticket}-retro.md`

## Branches and checkout

- Default base branch: `main`
- Implementation edits go in the checkout the user has open. One implementation per checkout at a time.
- Prototypes and experiments go in a scratch copy (for example a temporary worktree), never in the open checkout.

## Runtime

- <the runtime version CI uses. If the default on PATH differs, prefix commands with `PATH=<path to that version>/bin:$PATH`>

## Tests by phase

Fast checks while building, the full matrix once at the end.

| Phase | Runs | Commands |
|---|---|---|
| Implement | lint, unit tests related to the changed files, the new tests | `<lint command>`; `<unit tests related to the changed files>`; `<the new tests only>` |
| Implement, only when the plan names it | one integration suite that is the only proof of a criterion | `<command to run one integration suite> <suite pattern>` |
| Review code | no gate runs; a single test only to prove a criterion | `<run one test by name>` |
| Verify | the full matrix, in this order | 1. `<lint>` 2. `<unit suite>` 3. `<integration suite>` 4. UI, only when the UI changed: `<UI lint>`, `<UI tests>`, `<UI build>` |

## Test stack

Say which stack each kind of step runs on, with its ports, because the defaults usually point at the
stack you use day to day.

- Shared stack for reads and non-destructive checks: <services, ports>. Check it:
  `<command>`. Start it if it is down: `<command>`. Agents never reset, restart or reconfigure it.
- Steps that drop or reset their database (<the suites that do>): a throwaway stack the phase owns.
  - Start: `<command>`; it writes its connection settings to `<env file>`.
  - Point every step at it in the same command, because shell state does not carry between commands:
    `set -a; . <env file>; set +a; <test command>`. Override every key your `.env` sets.
  - Dead-port the side services the code calls by default (<env vars>), so a run cannot reach your
    local services.
  - Leak check: grep the logs for <the shared stack's ports> before reporting.
  - Expected noise on it, which is not a blocker: <log lines a missing optional service produces>.
  - Remove it before the phase ends, also on failure: `<command>`.
- Steps that bring their own database: <which>.

## Guard

Read by `~/.claude/pipeline/guard.py`. Containers and compose projects a pipeline agent may not stop,
remove, restart or write SQL into; ports that mark a shared stack; commands that reset whatever they
point at and so must name a throwaway target (`targetMarkers`: text that shows the command names one);
commands never to run.

```json
{
  "protectedNames": ["<container name prefix>", "<compose project>"],
  "sharedPorts": ["<shared stack port>"],
  "destructiveCommands": ["<a command that resets its database>"],
  "targetMarkers": ["<PORT_VARIABLE>=", "set -a"],
  "branchPattern": "^task/[0-9]+(-[a-z0-9]+)+$",
  "denyAlways": ["<a reset command for the shared stack>"]
}
```

## Invariants every phase checks

Rules a change in this repository must never break. Each phase checks the change against each one.

- <for example: every public API change stays backward compatible>
- <for example: generated files are never edited by hand>
- <for example: database migrations are append-only; never edit a released one>

## Docs and release notes

- <which docs or release-note files must change when user-visible behaviour changes>

## Known flaky tests

- `<test name>`: <symptom>. Re-run once in isolation before treating it as a failure, and name it in the report.

## Git conventions

- Commit subject: `<ticket> - <summary>`
- The agent never commits, branches or pushes. It prepares commit and PR text; a person commits.
- Do not commit while a code review or a verify run is in progress; it reads the working tree.
- <how prepared text is written: line wrapping, length, sections, words to avoid>
- Nothing published (tickets, PRs, replies) names the pipeline's own ids: finding ids, round numbers,
  gate names, plan section numbers.
- Never post to the ticket system or the PR without an explicit request; prepared text stays in the work
  directory.
