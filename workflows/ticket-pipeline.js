export const meta = {
  name: 'ticket-pipeline',
  description: 'Plan one ticket, review the plan (reviewer reports, a fresh planner applies) until a round raises no Critical or High, implement with fast checks, review the code, then run the full test matrix once and write the commit message and PR description. Or turn PR feedback into a fix round. Or take several tickets: check them for overlap, plan them in parallel, implement them one at a time on their own branches, waiting for your go between them. Never commits.',
  whenToUse: 'One ticket, a project profile in .claude/pipeline.local.md, and a checkout that holds no other uncommitted work. Pass the ticket (plus optional artifact URLs or file paths) as args; for PR feedback pass { mode: "pr-feedback", input: "<ticket>", pr: <number> }; for several tickets pass { mode: "multi", tickets: ["#1", "#2"] } and resume with the resumeArgs each stop returns.',
  phases: [
    { title: 'Overlap', detail: 'multi mode: one agent checks the tickets for shared code and dependencies' },
    { title: 'Plan', detail: 'one agent runs the plan-ticket skill (in multi mode, one per ticket, in parallel)' },
    { title: 'Review plan', detail: 'fresh reviewer per round reports; a fresh planner applies the findings' },
    { title: 'PR feedback', detail: 'read-only: review comments and CI into a classified fix list' },
    { title: 'Implement', detail: 'open checkout, fast checks only; fix rounds build on earlier edits' },
    { title: 'Review code', detail: 'closes out every criterion; runs no suites' },
    { title: 'Verify', detail: 'full test matrix once on the final tree' },
    { title: 'Git texts', detail: 'commit message and PR description for the change, Testing filled from verify' },
    { title: 'Retro', detail: 'records the run, escapes and lesson candidates; proposes changes, never makes them' },
  ],
}

// Two gates.
//  Gate 1 (automated, before any code): the latest plan-review round raised no new Critical or High
//  finding and left none open. Reviewers only report; a fresh planner agent applies each round's
//  findings, and the next round checks what it did. Base drift is information for the implementer,
//  not a plan finding. If the gate does not close within maxPlanRounds, the run stops with the
//  findings-per-round trend so a person decides; resume with { acceptPlan: true } to go on.
//  Gate 2 (human, before any commit): the pipeline never commits. A person reads the diff and commits.
// Tests are tiered: implementation runs fast checks, code review runs none, verification runs the full
// matrix once. A verification failure sends a fix round that re-runs only the failing tests, then
// verification again. Fix rounds after verification skip code review; gate 2 is where a person sees them.
// Each phase runs as a custom agent type from ~/.claude/agents/ (pipeline-*). The PreToolUse guard
// (~/.claude/pipeline/guard.py) keys on that type: reviewers and the verifier write only their report,
// nobody commits, pushes, posts to GitHub or resets a shared stack. Pass { agentTypes: false } to run
// without them (no guard).
// The agent types carry a tool allowlist: every tool a phase has used, and nothing else. The Artifact
// tool alone adds about 15k tokens to every turn, so it is in the pipeline-<role>-artifacts variants
// only. The first planner always gets it and reports the artifact links it found; later phases get it when the
// input or the planner names a link. A phase without it that meets a link stops and returns
// needsArtifactTool, and the step is restarted with the variant.
// Verdicts and counts are reported by agents. A workflow script cannot read files, so nothing here
// checks them deterministically; each check runs in its own agent, separate from the one it checks.

// Every run that changed code ends with the commit message and PR description written to
// <work>/<ticket>-git-texts.md (plan-ticket git-texts mode, Testing filled from the verify report); a run
// that stopped before implementation points at the plan's §12. The last log line names the file, and the
// result carries it as gitTexts.
// Every run, finished or stopped, ends with a retro agent that appends the run to
// pipeline-runs.jsonl in the work directory and keeps lesson candidates; pass { retro: false } to skip it.
// Multi mode ({ mode: "multi", tickets: [...] }): one agent checks the tickets for overlap first; then
// every ticket is planned and plan-reviewed in parallel, as in single mode; then the tickets are built in
// the order given, one at a time. Before each ticket the implementer creates its branch
// (task/<ticket>-<slug>) from the base, or from an earlier ticket's branch when the overlap check says
// it depends on it. After a ticket is green the run stops: a person reads the diff and commits it on that
// branch (gate 2), then resumes with the resumeArgs the stop returned. A stacked ticket whose base
// moved under its plan gets one plan-review round and revision before it is built.
// args: "<ticket> [artifact URLs] [file paths]" or
// { input, mode, pr, tickets, go, attempts, acceptPlans, skillDir, maxPlanRounds, maxFixRounds,
//   maxVerifyFixes, verifyEffort, pauseBeforeImplement, acceptPlan, agentTypes, retro, retroEffort }
const A = args && typeof args === 'object' ? args : {}
const MODE = A.mode || 'ticket'
const TICKETS = Array.isArray(A.tickets) ? A.tickets.map(String) : []
const INPUT = typeof args === 'string' ? args : A.input || (MODE === 'multi' ? TICKETS.join(' ') : '')
const GO = A.go || 1
const ATTEMPTS = A.attempts && typeof A.attempts === 'object' ? A.attempts : {}
const ACCEPT_PLANS = new Set((A.acceptPlans || []).map(String))
const PR = A.pr ? String(A.pr) : ''
const SKILL_DIR = A.skillDir || '~/.claude/skills'
const MAX_PLAN_ROUNDS = A.maxPlanRounds || 4
const MAX_FIX_ROUNDS = A.maxFixRounds || 2
const MAX_VERIFY_FIXES = A.maxVerifyFixes || 2
const VERIFY_EFFORT = A.verifyEffort || 'low'
const PAUSE_BEFORE_IMPLEMENT = !!A.pauseBeforeImplement
const ACCEPT_PLAN = !!A.acceptPlan
const AGENT_TYPES = A.agentTypes !== false
const RETRO = A.retro !== false
const RETRO_EFFORT = A.retroEffort || 'medium'

if (!String(INPUT).trim()) {
  throw new Error('ticket-pipeline: pass the ticket as args, e.g. "#123" or { input: "#123 <artifact-url>" }')
}
if (MODE !== 'ticket' && MODE !== 'pr-feedback' && MODE !== 'multi') {
  throw new Error(`ticket-pipeline: unknown mode "${MODE}"; use "ticket", "pr-feedback" or "multi"`)
}
if (MODE === 'multi' && TICKETS.length < 2) {
  throw new Error('ticket-pipeline: multi mode needs { tickets: ["#1", "#2", ...] } with two or more tickets')
}
if (MODE === 'pr-feedback' && !PR) {
  throw new Error('ticket-pipeline: pr-feedback mode needs { pr: <number or URL> }')
}

const ARTIFACT_LINK = /claude\.ai\/(?:code\/)?artifact\//i
const NO_ARTIFACT_VARIANT = new Set(['verifier', 'retro'])

// The ticket a step works on. Single mode has one, whose prompts and labels are exactly the ones this
// workflow always used. Multi mode has one per ticket, with a label prefix and a note about the batch.
const ticketContext = (input, prefix, note) =>
  ({ input, prefix: prefix || '', note: note || '', artifacts: ARTIFACT_LINK.test(String(input)) })
const SINGLE = ticketContext(INPUT)

const agentTypeFor = (role, withArtifacts) => {
  if (!AGENT_TYPES) {
    return {}
  }
  const variant = withArtifacts && !NO_ARTIFACT_VARIANT.has(role) ? '-artifacts' : ''
  return { agentType: `pipeline-${role}${variant}` }
}

// One phase step as the role's agent type. Restarts once with the Artifact tool when the lean
// variant reports that the work needs an artifact read.
async function run(T, role, prompt, opts, alwaysArtifacts) {
  const withArtifacts = !!alwaysArtifacts || T.artifacts
  const label = T.prefix + opts.label
  let result = await agent(prompt, { ...opts, label, ...agentTypeFor(role, withArtifacts) })
  if (result && result.needsArtifactTool && AGENT_TYPES && !withArtifacts && !NO_ARTIFACT_VARIANT.has(role)) {
    log(`${label}: needs the Artifact tool; restarting this step with it`)
    T.artifacts = true
    result = await agent(prompt, { ...opts, label: `${label} +artifacts`, ...agentTypeFor(role, true) })
  }
  return result
}

const NEEDS_ARTIFACT = {
  type: 'boolean',
  description: 'true only if the work needed a claude.ai artifact link read and you had no Artifact tool; you stopped without guessing',
}

// Pointers from the previous step's result. The agent still reads the report the skill names.
const pointers = (label, items) =>
  items && items.length
    ? ` ${label}, as the previous step reported them: ${items.join('; ')}. This list is a pointer into the report, not a replacement for reading it.`
    : ''

const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    planPath: { type: 'string' },
    baseSha: { type: 'string', description: 'base branch SHA recorded in the plan' },
    readiness: { type: 'string' },
    artifactLinks: { type: 'array', items: { type: 'string' }, description: 'every claude.ai artifact link in the input, the ticket, its epic or the notes they link' },
    summary: { type: 'string' },
  },
  required: ['planPath', 'readiness', 'artifactLinks', 'summary'],
}

const PLAN_REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['READY', 'NOT READY'] },
    newCritical: { type: 'number', description: 'CONFIRMED or PLAUSIBLE findings raised this round, regressions included' },
    newHigh: { type: 'number' },
    newMedium: { type: 'number' },
    newLow: { type: 'number' },
    openCritical: { type: 'number', description: 'open after this round, new and carried over' },
    openHigh: { type: 'number' },
    openMedium: { type: 'number' },
    openLow: { type: 'number' },
    baseMoved: { type: 'boolean', description: 'information only: the base moved since the plan SHA' },
    baseTouchedFiles: { type: 'array', items: { type: 'string' }, description: 'changed files on the base that the plan touches or depends on' },
    openFindingIds: { type: 'array', items: { type: 'string' }, description: 'open findings after this round, as "<id> <severity>"' },
    needsArtifactTool: NEEDS_ARTIFACT,
    summary: { type: 'string' },
  },
  required: ['verdict', 'newCritical', 'newHigh', 'openCritical', 'openHigh', 'summary'],
}

const REVISION_SCHEMA = {
  type: 'object',
  properties: {
    accepted: { type: 'number' },
    rejected: { type: 'number' },
    deferred: { type: 'number' },
    sectionsChanged: { type: 'array', items: { type: 'string' } },
    needsArtifactTool: NEEDS_ARTIFACT,
    summary: { type: 'string' },
  },
  required: ['accepted', 'rejected', 'deferred', 'summary'],
}

const PR_FEEDBACK_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['FIXES NEEDED', 'PLAN CHANGE NEEDED', 'NOTHING TO FIX'] },
    fixIds: { type: 'array', items: { type: 'string' }, description: 'FIX and CI REAL item ids' },
    criterionChangeIds: { type: 'array', items: { type: 'string' } },
    repliesPrepared: { type: 'number' },
    headMatches: { type: 'boolean', description: 'PR head SHA equals local HEAD' },
    needsArtifactTool: NEEDS_ARTIFACT,
    summary: { type: 'string' },
  },
  required: ['verdict', 'summary'],
}

const RETRO_SCHEMA = {
  type: 'object',
  properties: {
    recorded: { type: 'boolean', description: 'a line for this run was appended to the run log' },
    outcome: { type: 'string' },
    escapes: { type: 'number' },
    lessonsAdded: { type: 'number' },
    lessonsProposed: { type: 'array', items: { type: 'string' }, description: 'lesson ids with a proposed edit waiting for a person' },
    summary: { type: 'string' },
  },
  required: ['recorded', 'escapes', 'summary'],
}

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    status: { type: 'string' },
    baseMoved: { type: 'boolean', description: 'true if the base moved and touched files the plan depends on; nothing was edited' },
    movedFiles: { type: 'array', items: { type: 'string' } },
    changedFiles: { type: 'array', items: { type: 'string' } },
    needsArtifactTool: NEEDS_ARTIFACT,
    summary: { type: 'string' },
  },
  required: ['status', 'baseMoved', 'summary'],
}

const OVERLAP_SCHEMA = {
  type: 'object',
  properties: {
    reportPath: { type: 'string' },
    pairs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          first: { type: 'string' }, second: { type: 'string' },
          relation: { type: 'string', enum: ['independent', 'shared-files', 'dependent', 'same-work'] },
          files: { type: 'array', items: { type: 'string' } },
          note: { type: 'string' },
        },
        required: ['first', 'second', 'relation'],
      },
    },
    stackOn: {
      type: 'array',
      description: 'for each ticket that must be built on top of an earlier ticket in the given order: { ticket, on }',
      items: { type: 'object', properties: { ticket: { type: 'string' }, on: { type: 'string' } }, required: ['ticket', 'on'] },
    },
    orderConflict: { type: 'string', description: 'set when a ticket depends on one that comes later in the given order; empty otherwise' },
    artifactLinks: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['reportPath', 'pairs', 'stackOn', 'summary'],
}

const BRANCH_SCHEMA = {
  type: 'object',
  properties: {
    ready: { type: 'boolean', description: 'true if the branch exists, is checked out and the tree is clean' },
    branch: { type: 'string' },
    base: { type: 'string', description: 'the branch or commit it was created from' },
    reason: { type: 'string', enum: ['', 'dirty-tree', 'base-not-committed', 'base-missing', 'other'] },
    summary: { type: 'string' },
  },
  required: ['ready', 'summary'],
}

const CODE_REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['ALL ACS CLOSED', 'GAPS'] },
    openCount: { type: 'number' },
    openACs: { type: 'array', items: { type: 'string' } },
    openFindings: { type: 'array', items: { type: 'string' }, description: 'open findings, as "<id> <severity> <file:line>"' },
    headChanged: { type: 'boolean', description: 'true if HEAD or the tree changed during the review' },
    needsArtifactTool: NEEDS_ARTIFACT,
    summary: { type: 'string' },
  },
  required: ['verdict', 'openCount', 'summary'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['GREEN', 'RED', 'BLOCKED'] },
    failing: { type: 'array', items: { type: 'string' }, description: 'class change only: failing tests with their files, for the fix round' },
    baseFailing: { type: 'array', items: { type: 'string' }, description: 'class base: failing tests that also fail on the merge base or are known base failures' },
    environmentNamed: { type: 'array', items: { type: 'string' }, description: 'class environment: failures that passed on their re-run, with the reason' },
    flakyNamed: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['verdict', 'summary'],
}

// Agents read the skill file as text. Its frontmatter is not applied to them; this session's
// permission rules and the guard hook are.
const skillFor = (T, name, extra) =>
  `Read the skill file ${SKILL_DIR}/${name}/SKILL.md (expand ~ to the home directory) and follow it ` +
  `exactly, with this as its arguments: ${T.input}${extra ? ' ' + extra : ''}\n\n` + T.note
const skill = (name, extra) => skillFor(SINGLE, name, extra)

const planPrompt = (T) => skillFor(T, 'plan-ticket') +
  'Work to completion. Record the base branch SHA in §10. Do not commit, branch or push. ' +
  'Return the plan path, the base SHA, a one-line readiness verdict, and every claude.ai artifact link ' +
  'you found in the input, the ticket, its epic or the notes they link (read each one).'

const reviewPrompt = (T, r, extra) => skillFor(T, 'review-plan') + (extra || '') +
  `This is plan-review round ${r}. You are a fresh agent: read the ticket and the code first, write ` +
  'your own expectations, then the plan, then the analysis and the planner responses. Re-check every ' +
  'earlier finding against the current plan. Report only: do not edit the plan. Record base drift as ' +
  'information, not as a finding, unless it breaks something the plan relies on. Do not run full test ' +
  'suites. Return the new and open counts by severity.'

const revisePrompt = (T, r, open) => skillFor(T, 'plan-ticket', 'revise') +
  `Revise mode, after plan-review round ${r}. You are a fresh agent. Decide every open finding in that ` +
  'round (ACCEPTED, REJECTED with evidence, or DEFERRED for Medium and Low only), fold the accepted ones ' +
  'into the plan, and append the planner response to the review file. Do not commit, branch or push.' +
  pointers('Open findings', open)

const prFeedbackPrompt = skill('pr-feedback', PR) +
  'Read the PR feedback and CI results read only, classify every item against the code, and write the ' +
  'round to the feedback file. Prepare replies; never post anything to GitHub.'

const FIX_SOURCE = {
  review: { report: 'code-review', rerun: 'the fast checks for those files', after: 'code review' },
  verify: { report: 'verify', rerun: 'the tests that failed there', after: 'verification' },
  pr: { report: 'pr-feedback', rerun: 'the fast checks for those files, and any failing CI test it names', after: 'pull-request feedback' },
}

const implPrompt = (T, kind, n, items, branchNote) => {
  if (kind === 'first') {
    return skillFor(T, 'implement-plan') +
      'Implement in the checkout that is open. Check whether the base moved first. Run fast checks only.' +
      ' Do not commit, branch or push.' + (branchNote || '')
  }
  const s = FIX_SOURCE[kind]
  return skillFor(T, 'implement-plan') +
    `This is a fix round after ${s.after} (round ${n}). The working tree already holds your earlier edits ` +
    'for this ticket: build on them. Never stash, reset or check out over them. ' +
    `Read only the latest round of the ${s.report} file and the files it names, fix only what it lists ` +
    `as to fix, and re-run only ${s.rerun}. Do not commit, branch or push.` +
    pointers(kind === 'verify' ? 'Failing tests' : 'Items to fix', items)
}

const codeReviewPrompt = (T, i) => skillFor(T, 'review-code') +
  `This is code-review round ${i}. You are a fresh agent. Include untracked files in the change. ` +
  'Do not run the test suites; verification runs them.'

const verifyPrompt = (T, v) => skillFor(T, 'verify-ticket') +
  `This is verification run ${v}. Run the profile's full Verify matrix once, each step on the stack the ` +
  'profile names for it. Change no code.'

const counts = (v, kind) =>
  ['Critical', 'High', 'Medium', 'Low'].map((s) => v[`${kind}${s}`] ?? '?').join('/')

// 3 + 4. Implement (or fix) with fast checks, then review the code; repeat on gaps
async function buildAndReview(T, firstKind, context, firstItems, branchNote, labelSuffix) {
  const sfx = labelSuffix || ''
  let impl = null
  let lastCodeReview = null
  let closed = false
  for (let i = 1; i <= MAX_FIX_ROUNDS + 1 && !closed; i++) {
    phase('Implement')
    const kind = i === 1 ? firstKind : 'review'
    const n = i === 1 ? 1 : i - 1
    log(kind === 'first' ? 'Implement: starting' : `Fix round ${n} (${FIX_SOURCE[kind].after}): starting`)
    const items = i === 1
      ? firstItems
      : [...(lastCodeReview.openFindings || []), ...(lastCodeReview.openACs || []).map((ac) => `criterion not closed: ${ac}`)]
    impl = await run(T, 'implementer', implPrompt(T, kind, n, items, i === 1 ? branchNote : ''), {
      schema: IMPL_SCHEMA, phase: 'Implement',
      label: (kind === 'first' ? 'implement' : kind === 'pr' ? 'pr-fix' : `fix r${n}`) + sfx,
    })
    if (!impl) {
      return { stop: { stoppedAt: 'implement', ...context, lastCodeReview } }
    }
    if (impl.baseMoved) {
      log(`Implement: stopped, the base moved and touched ${(impl.movedFiles || []).join(', ') || 'planned files'}. Run a plan-review round, then resume.`)
      return { stop: { stoppedAt: 'base-moved', ...context, impl } }
    }
    log(`Implement: ${impl.status}. Code review round ${i}: starting`)
    lastCodeReview = await run(T, 'code-reviewer', codeReviewPrompt(T, i), {
      schema: CODE_REVIEW_SCHEMA, phase: 'Review code', label: `code-review r${i}${sfx}`,
    })
    if (!lastCodeReview) {
      return { stop: { stoppedAt: 'review-code', ...context, impl } }
    }
    const c = lastCodeReview
    closed = c.verdict === 'ALL ACS CLOSED' && c.openCount === 0
    log(`Code review round ${i}: ${c.verdict}, open: ${c.openCount}${c.headChanged ? ', HEAD changed during the review' : ''}`)
  }
  if (!closed) {
    log(`Still GAPS after ${MAX_FIX_ROUNDS} fix rounds. Over to you.`)
    return { stop: { stoppedAt: 'review-code', ...context, impl, lastCodeReview } }
  }
  return { impl, lastCodeReview }
}

// 5. Verify: the full matrix once; on RED, fix only the failing tests and verify again
async function verifyLoop(T, context) {
  let green = false
  let lastVerify = null
  let impl = null
  for (let v = 1; v <= MAX_VERIFY_FIXES + 1 && !green; v++) {
    if (v > 1) {
      phase('Implement')
      log(`Fix round ${v - 1} (verification): starting, failing: ${(lastVerify.failing || []).join(', ')}`)
      impl = await run(T, 'implementer', implPrompt(T, 'verify', v - 1, lastVerify.failing), {
        schema: IMPL_SCHEMA, phase: 'Implement', label: `verify-fix r${v - 1}`,
      })
      if (!impl) {
        return { stoppedAt: 'verify-fix', ...context, lastVerify }
      }
    }
    phase('Verify')
    log(`Verify run ${v}: starting`)
    lastVerify = await run(T, 'verifier', verifyPrompt(T, v), {
      schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify r${v}`, effort: VERIFY_EFFORT,
    })
    if (!lastVerify) {
      return { stoppedAt: 'verify', ...context }
    }
    const changeFailing = lastVerify.failing || []
    const baseFailing = lastVerify.baseFailing || []
    log(`Verify run ${v}: ${lastVerify.verdict}` +
      (changeFailing.length ? `, failing: ${changeFailing.join(', ')}` : '') +
      (baseFailing.length ? `, failing on the base too: ${baseFailing.join(', ')}` : '') +
      ((lastVerify.environmentNamed || []).length ? `, environment (passed on re-run): ${lastVerify.environmentNamed.join(', ')}` : ''))
    if (lastVerify.verdict === 'BLOCKED') {
      log('Verify is blocked. Read the verify file; fix the environment, then resume.')
      return { stoppedAt: 'verify-blocked', ...context, lastVerify }
    }
    green = lastVerify.verdict === 'GREEN'
    // A fix round on this ticket cannot fix what fails on the base as well: stop instead of looping.
    if (!green && !changeFailing.length && baseFailing.length) {
      log('Verify is RED only on failures the base has too. A fix round here cannot fix them: update the branch from the base, or wait for the base fix, then resume.')
      return { stoppedAt: 'verify-base-red', ...context, impl: impl || context.impl, lastVerify }
    }
  }
  if (!green) {
    log(`Still RED after ${MAX_VERIFY_FIXES} fix rounds. Over to you.`)
  }
  // Gate 2 is yours: read the diff, then commit.
  return { green, ...context, impl: impl || context.impl, lastVerify }
}

const GIT_TEXTS_SCHEMA = {
  type: 'object',
  properties: {
    path: { type: 'string', description: 'absolute path of the git-texts file you wrote' },
    commitMessage: { type: 'string' },
    prTitle: { type: 'string' },
    prDescription: { type: 'string' },
    summary: { type: 'string' },
  },
  required: ['path', 'commitMessage', 'prDescription', 'summary'],
}

const gitTextsPrompt = (T, outcome) => skillFor(T, 'plan-ticket', 'git-texts') +
  'Git-texts mode, for the run that just ended' +
  (outcome.feedback ? ' (PR-feedback mode: a fix round on an open PR)' : '') +
  `. The workflow reports its outcome as: ${JSON.stringify(briefOf(outcome))}. ` +
  'Write the git-texts file and return its path and the texts as written. Change no code and no plan. ' +
  'Do not commit, branch, push or post.'

// The commit message and PR description, or where to find them. Runs whenever code changed, whatever
// the verdict, so the last thing a run says is where the texts are.
async function gitTextsFor(T, outcome) {
  const changed = outcome.impl && (outcome.impl.changedFiles || []).length > 0
  if (!changed) {
    if (outcome.plan && outcome.plan.planPath) {
      return { path: outcome.plan.planPath, section: '§12', note: 'prepared in the plan; Testing is filled after verification' }
    }
    return null
  }
  phase('Git texts')
  log(`${T.prefix}Git texts: starting`)
  const texts = await run(T, 'planner', gitTextsPrompt(T, outcome), {
    schema: GIT_TEXTS_SCHEMA, phase: 'Git texts', label: 'git-texts', effort: 'low',
  })
  if (!texts) {
    if (outcome.plan && outcome.plan.planPath) {
      return { path: outcome.plan.planPath, section: '§12', note: 'the git-texts step did not finish; Testing placeholders are not filled' }
    }
    return null
  }
  return texts
}

const gitTextsLine = (T, g) => g
  ? `${T.prefix}Commit message and PR description: ${g.path}${g.section ? ` ${g.section}` : ''}${g.note ? ` (${g.note})` : ''}`
  : `${T.prefix}Commit message and PR description: none (no plan and no change in this run)`

const retroPrompt = (T, brief) => skillFor(T, 'pipeline-retro') +
  'Ticket mode, for the run that just ended. The workflow reports its outcome as: ' + JSON.stringify(brief) +
  '. Check it against the handoff files. Never edit a skill, an agent definition or the profile.'

// 1 + 2. Plan, then review the plan: the reviewer reports, a fresh planner applies, until gate 1 closes
async function planAndReview(T, accept) {
phase('Plan')
log(`${T.prefix}Plan: starting`)
const plan = await run(T, 'planner', planPrompt(T), { schema: PLAN_SCHEMA, phase: 'Plan', label: 'plan' }, true)
if (!plan) {
  return { stop: { stoppedAt: 'plan' } }
}
if ((plan.artifactLinks || []).length) {
  T.artifacts = true
}
log(`${T.prefix}Plan: written to ${plan.planPath}. ${plan.readiness}${T.artifacts ? ' Artifact links found: later phases get the Artifact tool.' : ''}`)

phase('Review plan')
let planReady = accept
let lastReview = null
let lastRevision = null
const trend = []
if (accept) {
  log(`${T.prefix}Plan accepted by hand (acceptPlan): skipping plan review.`)
}
for (let r = 1; r <= MAX_PLAN_ROUNDS && !planReady; r++) {
  log(`${T.prefix}Plan review round ${r}: starting`)
  lastReview = await run(T, 'plan-reviewer', reviewPrompt(T, r), {
    schema: PLAN_REVIEW_SCHEMA, phase: 'Review plan', label: `plan-review r${r}`,
  })
  if (!lastReview) {
    return { stop: { stoppedAt: 'plan-review', plan, trend } }
  }
  const v = lastReview
  planReady = v.newCritical === 0 && v.newHigh === 0 && v.openCritical === 0 && v.openHigh === 0
  trend.push(`r${r}: new C/H/M/L ${counts(v, 'new')}, open ${counts(v, 'open')}${v.baseMoved ? ', base moved' : ''}`)
  log(`${T.prefix}Plan review round ${r}: ${v.verdict}; ${trend[trend.length - 1]}${planReady ? '; gate 1 closed' : ''}`)
  const openAny = (v.openCritical || 0) + (v.openHigh || 0) + (v.openMedium || 0) + (v.openLow || 0) > 0
  if (openAny && (planReady || r < MAX_PLAN_ROUNDS)) {
    log(`${T.prefix}Plan revision after round ${r}: starting`)
    lastRevision = await run(T, 'planner', revisePrompt(T, r, v.openFindingIds), {
      schema: REVISION_SCHEMA, phase: 'Review plan', label: `plan-revise r${r}`,
    })
    if (!lastRevision) {
      return { stop: { stoppedAt: 'plan-revise', plan, lastReview, trend } }
    }
    log(`${T.prefix}Plan revision after round ${r}: accepted ${lastRevision.accepted}, rejected ${lastRevision.rejected}, deferred ${lastRevision.deferred}`)
    if (planReady) {
      log(`${T.prefix}Gate 1 closed with Medium/Low findings open; their folds were not re-reviewed. Gate 2 sees them.`)
    }
  }
}
if (!planReady) {
  log(`${T.prefix}Plan not ready after ${MAX_PLAN_ROUNDS} rounds. Findings per round:`)
  trend.forEach((t) => log(`  ${t}`))
  log('Read the review file and decide. To go on with this plan, resume with { acceptPlan: true }' + (MODE === 'multi' ? ' or acceptPlans: ["<ticket>"].' : '.'))
  return { stop: { stoppedAt: 'plan-review', plan, lastReview, lastRevision, trend } }
}
return { plan, lastReview, lastRevision, trend, planReady }
}

async function pipelineMain() {
// PR feedback: read the PR, then a fix round, code review and verification
if (MODE === 'pr-feedback') {
  phase('PR feedback')
  log(`PR feedback: reading PR ${PR}`)
  const feedback = await run(SINGLE, 'pr-feedback', prFeedbackPrompt, {
    schema: PR_FEEDBACK_SCHEMA, phase: 'PR feedback', label: 'pr-feedback',
  })
  if (!feedback) {
    return { stoppedAt: 'pr-feedback' }
  }
  log(`PR feedback: ${feedback.verdict}${(feedback.fixIds || []).length ? `, to fix: ${feedback.fixIds.join(', ')}` : ''}${feedback.headMatches === false ? ', PR head differs from local HEAD' : ''}`)
  if (feedback.verdict === 'NOTHING TO FIX') {
    log('Nothing to fix. The prepared replies are in the feedback file; a person posts them.')
    return { stoppedAt: 'nothing-to-fix', feedback }
  }
  if (feedback.verdict === 'PLAN CHANGE NEEDED') {
    log(`A comment changes an acceptance criterion (${(feedback.criterionChangeIds || []).join(', ')}). Revise the plan first.`)
    return { stoppedAt: 'plan-change', feedback }
  }
  const built = await buildAndReview(SINGLE, 'pr', { feedback }, feedback.fixIds)
  if (built.stop) {
    return built.stop
  }
  return await verifyLoop(SINGLE, { feedback, impl: built.impl, lastCodeReview: built.lastCodeReview, closed: true })
}

const plan1 = await planAndReview(SINGLE, ACCEPT_PLAN)
if (plan1.stop) {
  return plan1.stop
}
const { plan, lastReview, lastRevision, trend, planReady } = plan1

// Optional pause. Implementations share the open checkout. To plan several tickets at once, start one
// run per ticket with pauseBeforeImplement: true, then resume them one at a time with resumeFromRunId
// and pauseBeforeImplement: false; the finished phases come from cache.
if (PAUSE_BEFORE_IMPLEMENT) {
  log('Paused before implementation, as requested.')
  return { stoppedAt: 'before-implement', plan, lastReview, lastRevision, trend }
}

const built = await buildAndReview(SINGLE, 'first', { plan, lastReview, trend })
if (built.stop) {
  return built.stop
}
return await verifyLoop(SINGLE, { planReady, closed: true, plan, lastReview, trend, impl: built.impl, lastCodeReview: built.lastCodeReview })
}

const briefOf = (outcome) => ({
  mode: MODE === 'multi' ? 'ticket' : MODE,
  stoppedAt: outcome.stoppedAt || null,
  planReady: outcome.planReady ?? null,
  planTrend: outcome.trend || [],
  codeReview: outcome.lastCodeReview ? outcome.lastCodeReview.verdict : null,
  verify: outcome.lastVerify ? outcome.lastVerify.verdict : null,
  prFeedback: outcome.feedback ? outcome.feedback.verdict : null,
  green: outcome.green ?? null,
})

async function retroFor(T, outcome) {
  if (!RETRO) {
    return null
  }
  phase('Retro')
  log(`${T.prefix}Retro: starting`)
  const retro = await run(T, 'retro', retroPrompt(T, briefOf(outcome)), { schema: RETRO_SCHEMA, phase: 'Retro', label: 'retro', effort: RETRO_EFFORT })
  if (retro) {
    log(`${T.prefix}Retro: escapes ${retro.escapes}, lessons added ${retro.lessonsAdded ?? 0}${(retro.lessonsProposed || []).length ? `, proposals waiting for you: ${retro.lessonsProposed.join(', ')}` : ''}`)
  }
  return retro
}

// Multi mode: overlap check, parallel planning, then one ticket at a time on its own branch.
const ticketId = (t) => (String(t).match(/\d+/) || [String(t)])[0]

const overlapPrompt = (T) => skillFor(T, 'ticket-overlap') +
  `The tickets, in the order the person gave them (keep this order): ${TICKETS.join(', ')}. Read every ` +
  'ticket and the code it touches, write the overlap report, and return the relation of every pair and ' +
  'which tickets must be built on top of an earlier one. Change no code. Do not commit, branch or push.'

const batchNote = (t, overlap) => {
  const others = TICKETS.filter((x) => x !== t).join(', ')
  const stack = (overlap.stackOn || []).find((s) => ticketId(s.ticket) === ticketId(t))
  return `This ticket is one of a batch planned together: ${others} ${others.includes(',') ? 'are' : 'is'} planned in parallel ` +
    `and built after or before it in the order ${TICKETS.join(', ')}. Read the overlap report ${overlap.reportPath} ` +
    'first and keep to this ticket\'s scope: where the report gives a file or a behaviour to another ticket, depend ' +
    'on it instead of doing it twice. ' +
    (stack ? `The report stacks this ticket on ${stack.on}: it will be built on ${stack.on}'s branch, after ${stack.on} ` +
      `is committed, so plan on top of ${stack.on}'s planned changes and record in §10 that the base is ${stack.on}'s branch. ` : '') +
    'Do not create branches; the pipeline creates them before implementation.\n\n'
}

const branchPrompt = (T, base, stackedOn, attempt) => skillFor(T, 'implement-plan') +
  `Branch step only, attempt ${attempt}. Do not implement anything yet. 1) Run git status --porcelain: if it ` +
  'lists anything, stop with ready false and reason dirty-tree (the person must commit or clean the earlier ' +
  `ticket first). 2) git fetch origin. 3) The base is ${base}` +
  (stackedOn ? `, the branch of ${stackedOn}: check that it has commits beyond the profile's base branch ` +
    '(git log --oneline origin/<base branch>..<that branch>); if it has none, stop with ready false and reason ' +
    'base-not-committed. ' : '. ') +
  '4) Create the branch the profile names for this ticket (task/<ticket number>-<two to five lowercase words ' +
  `from the ticket title>) from that base with git switch -c <branch> ${base}, and confirm git branch --show-current. ` +
  'Return ready, the branch name and the base. Do not commit or push.'

const branchNote = (br, stackedOn) =>
  ` You are on branch ${br.branch}, created for this ticket from ${br.base}; do not create or switch branches.` +
  (stackedOn ? ` This ticket is stacked on ${stackedOn}: for the "has the base moved" check, the base is ` +
    `${br.base}, and ${stackedOn}'s commits on it count as base movement.` : '')

const refreshPrompt = (T, base, stackedOn) => reviewPrompt(T, 'refresh',
  `The base moved under this plan: the ticket is now built on ${base}` +
  (stackedOn ? `, which holds ${stackedOn}'s commit` : '') +
  '. Review the plan against that base: what the earlier ticket already did, what conflicts with it, and ' +
  'what the plan must change. ')

async function multiMain() {
  const batch = ticketContext(INPUT, 'batch ')
  phase('Overlap')
  log(`Overlap: checking ${TICKETS.join(', ')}`)
  const overlap = await run(batch, 'planner', overlapPrompt(batch), { schema: OVERLAP_SCHEMA, phase: 'Overlap', label: 'overlap' }, true)
  if (!overlap) {
    return { mode: 'multi', stoppedAt: 'overlap' }
  }
  const same = (overlap.pairs || []).filter((p) => p.relation === 'same-work')
  log(`Overlap: ${(overlap.pairs || []).map((p) => `${p.first}/${p.second} ${p.relation}`).join('; ')}` +
    `${(overlap.stackOn || []).length ? `; stacked: ${overlap.stackOn.map((x) => `${x.ticket} on ${x.on}`).join(', ')}` : ''}`)
  if (same.length || overlap.orderConflict) {
    log(same.length
      ? `Overlap: ${same.map((p) => `${p.first} and ${p.second}`).join(', ')} look like the same work. Decide whether to merge them, then start again.`
      : `Overlap: order conflict: ${overlap.orderConflict}. Reorder the tickets and start again.`)
    return { mode: 'multi', stoppedAt: same.length ? 'overlap-same-work' : 'overlap-order', overlap }
  }

  const Ts = TICKETS.map((t) => {
    const T = ticketContext(t, `${ticketId(t)} `, batchNote(t, overlap))
    T.artifacts = T.artifacts || (overlap.artifactLinks || []).length > 0
    return T
  })
  const planned = await parallel(Ts.map((T, i) => () =>
    planAndReview(T, ACCEPT_PLAN || ACCEPT_PLANS.has(TICKETS[i]) || ACCEPT_PLANS.has(ticketId(TICKETS[i])))))
  const notReady = planned.map((p, i) => (!p || p.stop ? TICKETS[i] : null)).filter(Boolean)
  if (notReady.length) {
    for (let i = 0; i < Ts.length; i++) {
      if (planned[i] && planned[i].stop) {
        await retroFor(Ts[i], planned[i].stop)
      }
    }
    log(`Plans not ready: ${notReady.join(', ')}. Read their review files; to go on, resume with acceptPlans for those tickets.`)
    return {
      mode: 'multi', stoppedAt: 'plan-review', overlap, notReady,
      plans: planned.map((p, i) => ({ ticket: TICKETS[i], ready: !!(p && !p.stop), trend: p && (p.trend || (p.stop && p.stop.trend)) })),
      resumeArgs: { ...A, acceptPlans: [...ACCEPT_PLANS, ...notReady] },
    }
  }
  if (PAUSE_BEFORE_IMPLEMENT) {
    log('All plans passed gate 1. Paused before implementation, as requested.')
    return { mode: 'multi', stoppedAt: 'before-implement', overlap, resumeArgs: { ...A, pauseBeforeImplement: false } }
  }

  const done = []
  for (let k = 0; k < Ts.length; k++) {
    const T = Ts[k]
    const t = TICKETS[k]
    if (k + 1 > GO) {
      const prev = done[k - 1]
      log(`${ticketId(prev.ticket)} is green on ${prev.branch}. Read the diff, commit it on that branch, then resume with go: ${k + 1} to start ${t}.`)
      return { mode: 'multi', stoppedAt: 'awaiting-go', next: t, done, resumeArgs: { ...A, go: k + 1 } }
    }
    const stack = (overlap.stackOn || []).find((s) => ticketId(s.ticket) === ticketId(t))
    const stackedOn = stack ? stack.on : null
    const parent = stackedOn ? done.find((d) => ticketId(d.ticket) === ticketId(stackedOn)) : null
    const base = parent ? parent.branch : 'origin/<the profile\'s base branch>'
    const attempt = ATTEMPTS[String(k + 1)] || 1

    phase('Implement')
    log(`${T.prefix}Branch: creating from ${base}${attempt > 1 ? ` (attempt ${attempt})` : ''}`)
    const br = await run(T, 'implementer', branchPrompt(T, base, parent ? stackedOn : null, attempt),
      { schema: BRANCH_SCHEMA, phase: 'Implement', label: `branch a${attempt}` })
    if (!br || !br.ready) {
      const why = br ? `${br.reason || 'other'}: ${br.summary}` : 'the branch step did not finish'
      log(`${T.prefix}Branch not created (${why}). Fix that, then resume with the resumeArgs.`)
      return { mode: 'multi', stoppedAt: 'branch', ticket: t, done, branch: br,
        resumeArgs: { ...A, go: k + 1, attempts: { ...ATTEMPTS, [String(k + 1)]: attempt + 1 } } }
    }
    log(`${T.prefix}Branch: ${br.branch} from ${br.base}`)

    const ctx = { plan: planned[k].plan, lastReview: planned[k].lastReview, trend: planned[k].trend }
    let built = await buildAndReview(T, 'first', ctx, null, branchNote(br, parent ? stackedOn : null))
    if (built.stop && built.stop.stoppedAt === 'base-moved') {
      phase('Review plan')
      log(`${T.prefix}Base moved under the plan: one plan-review round against ${br.base}, then a revision`)
      const refresh = await run(T, 'plan-reviewer', refreshPrompt(T, br.base, parent ? stackedOn : null),
        { schema: PLAN_REVIEW_SCHEMA, phase: 'Review plan', label: 'plan-review refresh' })
      const blocking = !refresh || refresh.openCritical > 0 || refresh.openHigh > 0
      const revision = refresh ? await run(T, 'planner', revisePrompt(T, 'refresh', refresh.openFindingIds),
        { schema: REVISION_SCHEMA, phase: 'Review plan', label: 'plan-revise refresh' }) : null
      if (!revision || (blocking && revision.rejected > 0)) {
        log(`${T.prefix}The refreshed plan still has open Critical or High findings. Over to you.`)
        const stop = { stoppedAt: 'base-moved', ...ctx, refresh, revision }
        await retroFor(T, stop)
        return { mode: 'multi', stoppedAt: 'base-moved', ticket: t, done, refresh, resumeArgs: { ...A, go: k + 1 } }
      }
      built = await buildAndReview(T, 'first', ctx, null, branchNote(br, parent ? stackedOn : null), ' after refresh')
    }
    if (built.stop) {
      const gitTexts = await gitTextsFor(T, built.stop)
      await retroFor(T, built.stop)
      log(gitTextsLine(T, gitTexts))
      return { mode: 'multi', ...built.stop, ticket: t, done, gitTexts, resumeArgs: { ...A, go: k + 1 } }
    }
    const verified = await verifyLoop(T, { planReady: true, closed: true, ...ctx, impl: built.impl, lastCodeReview: built.lastCodeReview })
    const gitTexts = await gitTextsFor(T, verified)
    const retro = await retroFor(T, verified)
    done.push({ ticket: t, branch: br.branch, base: br.base, green: !!verified.green, stoppedAt: verified.stoppedAt || null, gitTexts, retro })
    log(gitTextsLine(T, gitTexts))
    if (!verified.green) {
      log(`${T.prefix}Not green (${verified.stoppedAt || 'RED'}). Over to you; the later tickets wait.`)
      return { mode: 'multi', stoppedAt: verified.stoppedAt || 'verify-red', ticket: t, done, resumeArgs: { ...A, go: k + 1 } }
    }
  }
  const last = done[done.length - 1]
  log(`All ${done.length} tickets are green. Commit ${ticketId(last.ticket)} on ${last.branch}.`)
  return { mode: 'multi', done, finished: true }
}

if (MODE === 'multi') {
  return await multiMain()
}
const outcome = await pipelineMain()
const gitTexts = await gitTextsFor(SINGLE, outcome)
const retro = await retroFor(SINGLE, outcome)
log(gitTextsLine(SINGLE, gitTexts))
return RETRO ? { ...outcome, gitTexts, retro } : { ...outcome, gitTexts }
