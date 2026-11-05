#!/bin/zsh -l
# Runs /repo-pulse headless in one repository checkout. Meant for a weekly launchd job; safe to run by hand.
# Usage: run-repo-pulse.sh <repository checkout>
# The whole session runs under the pipeline guard as pipeline-learner (PIPELINE_GUARD_ROLE), so neither
# the session nor its agent can commit, push, post to GitHub or write outside the pulse's own files.
set -u
REPO=${1:?usage: run-repo-pulse.sh <repository checkout>}
LOG="$HOME/.claude/pipeline/repo-pulse.log"
CL=$(ls -d "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)
if [ ! -x "${CL:-}" ]; then
    CL=$(command -v claude || true)
fi
if [ -z "${CL:-}" ]; then
    echo "$(date '+%F %T') repo pulse: no claude binary found" >> "$LOG"
    exit 1
fi
cd "$REPO" || exit 1
export PIPELINE_GUARD_ROLE=pipeline-learner
{
    echo "== $(date '+%F %T') $REPO ($CL)"
    "$CL" -p "/repo-pulse" --permission-mode acceptEdits --output-format text \
        --allowedTools "Read" "Write" "Edit" \
        "Bash(git fetch:*)" "Bash(git log:*)" "Bash(git show:*)" "Bash(git grep:*)" "Bash(git rev-parse:*)" \
        "Bash(git cat-file:*)" "Bash(git diff:*)" "Bash(git ls-files:*)" "Bash(git remote:*)" \
        "Bash(gh pr view:*)" "Bash(gh pr list:*)" "Bash(gh api:*)" "Bash(/usr/bin/python3:*)" \
        "Bash(ls:*)" "Bash(cat:*)" "Bash(grep:*)" "Bash(sed -n:*)" "Bash(head:*)" "Bash(tail:*)" \
        "Bash(wc:*)" "Bash(jq:*)" "Bash(date:*)" "Bash(mkdir:*)" < /dev/null
    echo "exit=$?"
} >> "$LOG" 2>&1
osascript -e 'display notification "Knowledge file and proposals are in the work directory." with title "Repo pulse finished"' >/dev/null 2>&1 || true
