---
name: ticket-overlap
description: Before several tickets go through the pipeline together. Reads every ticket and the code it touches, and reports for each pair whether they are independent, share files, depend on each other or are the same work, and which ticket must be built on top of which. Changes no code. Run it as /ticket-overlap <ticket> <ticket> [...], in the order you want them built.
argument-hint: <ticket> <ticket> [...]
disable-model-invocation: true
# context and agent apply when a person runs the skill: it runs as that pipeline agent, in a fresh
# context, under the guard. The workflow ignores them; it starts its own agents and passes this file as text.
context: fork
agent: pipeline-planner-artifacts
# allowed-tools only pre-approves these tools for the turn that runs this skill.
# It restricts nothing. Use permission rules or disallowed-tools to restrict.
allowed-tools:
  - Read
  - Write
  - WebFetch
---

# Overlap check: can these tickets be planned in parallel and built one after another?

Several tickets planned at the same time go wrong in two ways: two plans change the same code in
different ways, or a later ticket is planned against a codebase an earlier one is about to change. This
check finds both before any plan is written.

## 0. Load the project profile

Read `.claude/pipeline.local.md` for the ticket command, the work directory and the base branch. If it is
missing, stop.

## Input

`$ARGUMENTS`: two or more tickets, in the order the person wants them built. Keep that order; you may
report a conflict with it, never change it.

## What to do

1. **Each ticket's surface.** Read the ticket and its parent epic, and the code it will touch: follow the
   real control flow from the entry points the ticket names. Write down, per ticket, the files, modules,
   tables, routes, configuration and behaviour it will change, and the ones it only relies on.
2. **Each pair.** Classify it:
   - `independent`: no file or behaviour in common;
   - `shared-files`: they change the same files in separate places, and either can go first;
   - `dependent`: one needs the other's change to build, to pass its tests or to behave as its ticket
     says, or both change the same lines or the same contract;
   - `same-work`: they ask for the same change, or one is contained in the other.
   Name the shared files and, for `dependent`, which ticket needs which and why, with `file:line`.
3. **Stacking.** A ticket that depends on an earlier ticket in the given order is stacked on it: it will be
   branched from that ticket's branch after that ticket is committed. If a ticket depends on one that
   comes later in the given order, that is an order conflict: describe it; the person decides.
4. **Split for shared code.** Where two tickets touch the same file or behaviour, say which ticket owns
   each part, so the plans do not do it twice.

## Write the report

To `<work>/overlap-<ticket>-<ticket>[...].md` (ticket numbers in the given order): the surface per ticket,
a table with one row per pair (relation, shared files, note), the stacking, the ownership split for shared
code, any order conflict, and every claude.ai artifact link the tickets name.

## Rules

- Read only towards the ticket system and GitHub. Change no code. Never commit, branch or push.
- Write only the overlap report.

## Output

The relation of every pair, the stacking, any order conflict, and the report path.
