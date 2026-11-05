#!/usr/bin/env python3
"""PreToolUse guard for ticket-pipeline agents.

Runs on every Bash, Write, Edit and NotebookEdit call, and acts only when the call comes from a
subagent whose type starts with "pipeline-" (the custom agents in ~/.claude/agents/), or from a session
whose launcher set PIPELINE_GUARD_ROLE (a scheduled headless run). Every other call, including an
ordinary main session, passes untouched.

What it refuses, for every pipeline agent:
  - git that writes history, branches or the stash, or throws away working-tree edits (the
    implementer alone may create a ticket branch named by the profile's branchPattern, from a clean tree);
  - GitHub writes (gh pr/issue/release writes, gh api / curl with a write method);
  - Docker commands that stop, remove or rewrite the shared stacks the profile protects,
    and daemon-wide prunes;
  - destructive test or bootstrap commands that would hit a shared stack: a command the profile
    lists as destructive must name its own target (an env file it sources, or a target variable set inline),
    and must not name a shared port.
What it refuses by role (file writes through Write, Edit, NotebookEdit, and shell redirects):
  - reviewers, the verifier and pr-feedback write only their own report file;
  - retro and the learner write only their few named files in the work directory;
  - planner and implementer write only inside the repository, never under .git/ or .claude/.
Scratch directories (/tmp, /private/tmp, $TMPDIR, /var/folders) are always writable.

Repository specifics (protected container names, shared ports, destructive commands) come from the
first ```json block under "## Guard" in <main checkout>/.claude/pipeline.local.md.

A refusal is a PreToolUse "deny" with the reason; the agent reads it and adapts. Each refusal is
appended to ~/.claude/pipeline/guard.log as one JSON line, with secrets redacted.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

LOG_PATH = os.environ.get("PIPELINE_GUARD_LOG") or os.path.expanduser("~/.claude/pipeline/guard.log")

REPORT_SUFFIX = {
    "pipeline-plan-reviewer": "-plan-review.md",
    "pipeline-code-reviewer": "-code-review.md",
    "pipeline-verifier": "-verify.md",
    "pipeline-pr-feedback": "-pr-feedback.md",
}
BUILDER_ROLES = ("pipeline-planner", "pipeline-implementer")
# Roles that keep a few named files and nothing else: role -> (file-name pattern, description).
NAMED_FILES = {
    "pipeline-retro": (r"^(pipeline-runs\.jsonl|pipeline-lessons\.md|.+-retro\.md)$",
                       "pipeline-runs.jsonl, pipeline-lessons.md and its *-retro.md report"),
    "pipeline-learner": (r"^(repo-knowledge\.md|repo-pulse-state\.json|repo-pulse-\d{4}-\d{2}-\d{2}\.md)$",
                         "repo-knowledge.md, repo-pulse-state.json and its repo-pulse-<date>.md report"),
}

DEFAULT_CONFIG = {
    "protectedNames": [],
    "sharedPorts": [],
    "destructiveCommands": [],
    "targetMarkers": ["set -a"],
    "denyAlways": [],
}

SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\||\n|\$\(|`")


def scratch_roots():
    roots = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders"]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)
    return [os.path.realpath(r) for r in roots]


def git(cwd, *args):
    try:
        out = subprocess.run(
            ["git", "-C", cwd] + list(args),
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def repo_roots(cwd):
    """The checkout cwd is in, and the main checkout (they differ inside a worktree)."""
    top = git(cwd, "rev-parse", "--show-toplevel")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    main = os.path.dirname(common) if common else ""
    roots = [os.path.realpath(p) for p in (top, main) if p]
    return roots, (os.path.realpath(main) if main else (roots[0] if roots else ""))


def load_config(main_root):
    config = dict(DEFAULT_CONFIG)
    if not main_root:
        return config
    path = os.path.join(main_root, ".claude", "pipeline.local.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return config
    section = re.search(r"^## Guard\b(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not section:
        return config
    block = re.search(r"```json\s*\n(.*?)\n```", section.group(1), re.S)
    if not block:
        return config
    loaded = json.loads(block.group(1))
    for key, value in loaded.items():
        config[key] = value
    return config


def inside(path, roots):
    for root in roots:
        if root and (path == root or path.startswith(root.rstrip("/") + "/")):
            return True
    return False


def resolve(path, cwd):
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    parent = os.path.realpath(os.path.dirname(path))
    return os.path.join(parent, os.path.basename(path))


def write_refusal(role, path, cwd, roots, main_root):
    """Reason to refuse writing `path` for `role`, or None."""
    target = resolve(path, cwd)
    if target == "/dev/null" or inside(target, scratch_roots()):
        return None
    if role in NAMED_FILES:
        pattern, what = NAMED_FILES[role]
        if inside(target, roots) and re.match(pattern, os.path.basename(target)):
            return None
        return (f"{role} writes only {what} in the work directory, not {target}. It proposes changes to "
                "memory, skills and the profile; a person makes them.")
    if role in REPORT_SUFFIX:
        suffix = REPORT_SUFFIX[role]
        if target.endswith(suffix) and inside(target, roots):
            return None
        return (f"{role} reports only: it may write its own report (*{suffix}) in the work directory "
                f"and scratch files, not {target}. Fixes go back through implement-plan.")
    if role in BUILDER_ROLES:
        if not inside(target, roots):
            return f"{role} writes only inside the repository or scratch directories, not {target}."
        for root in roots:
            for private in (".git", ".claude"):
                if inside(target, [os.path.join(root, private)]):
                    return f"{role} must not write under {private}/ ({target})."
        return None
    return f"unknown pipeline role {role}: refusing the write to {target}."


GIT_READONLY_STASH = re.compile(r"\bstash\s+(list|show)\b")


def branch_creation_refusal(rest, role, cwd, config):
    """The implementer may create a ticket branch: plain -b / -c only, a name matching the profile's
    branchPattern, and only from a clean working tree, so no one's uncommitted work changes branch."""
    if role != "pipeline-implementer" or not config.get("branchPattern"):
        return "creating branches is reserved for a person."
    m = re.match(r"\s*(?:-b|-c|--create)\s+(\S+)(?:\s+(\S+))?\s*$", rest)
    if not m:
        return "branch creation here must be exactly `git switch -c <branch> [<start>]` or `git checkout -b <branch> [<start>]`."
    name = m.group(1)
    if not re.match(config["branchPattern"], name):
        return f"branch name {name} does not match the profile's branchPattern ({config['branchPattern']})."
    status = git(cwd or os.getcwd(), "status", "--porcelain")
    if status:
        return "the working tree has uncommitted changes; the person commits or cleans the earlier work before a new branch."
    return None


def git_refusal(seg, role=None, cwd=None, config=None):
    m = re.search(r"\bgit\b(?:\s+-C\s+\S+|\s+-c\s+\S+|\s+--no-pager|\s+--git-dir=\S+|\s+--work-tree=\S+)*\s+([a-z-]+)(.*)", seg)
    if not m:
        return None
    sub, rest = m.group(1), m.group(2)
    if sub in ("commit", "push", "rebase", "cherry-pick", "revert", "am", "filter-branch", "update-ref", "replace"):
        return f"git {sub} is reserved for a person (gate 2). Prepare the text instead."
    if sub == "merge":
        return "git merge is reserved for a person."
    if sub == "stash" and not GIT_READONLY_STASH.search(seg):
        return "git stash would move this ticket's uncommitted edits (and the stash list is shared with the main checkout)."
    if sub == "reset" and re.search(r"--(hard|merge|keep)\b", rest):
        return "git reset --hard/--merge/--keep throws away working-tree edits."
    if sub == "clean" and re.search(r"(^|\s)-[a-zA-Z]*f|--force", rest):
        return "git clean -f deletes untracked files (new tests and migrations live there)."
    if sub == "checkout":
        if re.search(r"(^|\s)(-B|--orphan)\b", rest):
            return "creating branches is reserved for a person."
        if re.search(r"(^|\s)-b\b", rest):
            return branch_creation_refusal(rest, role, cwd, config or {})
        if re.search(r"(^|\s)--(\s|$)|(^|\s)\.(\s|$)", rest):
            return "git checkout -- <path> / git checkout . discards working-tree edits."
    if sub == "restore":
        if re.search(r"--worktree\b", rest) or not re.search(r"--staged\b", rest):
            return "git restore discards working-tree edits."
    if sub == "switch" and re.search(r"(^|\s)(-C|--force-create|--orphan)\b", rest):
        return "creating branches is reserved for a person."
    if sub == "switch" and re.search(r"(^|\s)(-c|--create)\b", rest):
        return branch_creation_refusal(rest, role, cwd, config or {})
    if sub == "branch":
        if re.search(r"(^|\s)(-d|-D|-m|-M|-c|-C|-f|-u|--delete|--move|--copy|--force|--track|--no-track|"
                     r"--set-upstream-to\S*|--unset-upstream|--edit-description)(\s|=|$)", rest):
            return "git branch with a write flag is reserved for a person."
        positional = [a for a in rest.split() if not a.startswith("-")]
        if positional and not re.search(r"(^|\s)(-l|--list|--contains|--no-contains|--merged|--no-merged|--points-at)\b", rest):
            return "creating branches is reserved for a person."
    if sub == "tag" and rest.strip() and not re.search(r"(^|\s)(-l|--list|--contains|--points-at)\b", rest):
        return "git tag is reserved for a person."
    if sub == "apply" and not re.search(r"--(check|stat|numstat|summary)\b", rest):
        return "git apply: no patch files in this pipeline; edit the files directly."
    return None


GH_WRITES = {
    "pr": {"create", "merge", "close", "reopen", "comment", "review", "edit", "ready", "lock", "unlock", "update-branch"},
    "issue": {"create", "comment", "edit", "close", "reopen", "delete", "transfer", "lock", "unlock", "pin", "unpin", "develop"},
    "release": {"create", "delete", "edit", "upload", "delete-asset"},
    "repo": {"create", "delete", "edit", "fork", "rename", "archive", "sync"},
    "workflow": {"run", "enable", "disable"},
    "run": {"rerun", "cancel", "delete"},
    "label": {"create", "edit", "delete", "clone"},
    "gist": {"create", "edit", "delete"},
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
}


def gh_refusal(seg):
    m = re.search(r"\bgh\s+([a-z-]+)(?:\s+([a-z-]+))?(.*)", seg)
    if m:
        group, verb, rest = m.group(1), m.group(2) or "", m.group(3)
        if group in GH_WRITES and verb in GH_WRITES[group]:
            return f"gh {group} {verb} writes to GitHub. Pipeline agents prepare text; a person posts it."
        if group == "api":
            full = (verb + rest)
            method = re.search(r"(?:-X|--method)\s*=?\s*([A-Za-z]+)", full)
            if method and method.group(1).upper() != "GET":
                return "gh api with a write method. Pipeline agents only read GitHub."
            if re.search(r"\bgraphql\b", full):
                if re.search(r"\bmutation\b", full):
                    return "gh api graphql mutation writes to GitHub."
            elif re.search(r"(^|\s)(-f|-F|--field|--raw-field|--input)\b", full) and not method:
                return "gh api with fields defaults to POST. Pass -X GET for a read."
    if re.search(r"\bcurl\b", seg) and re.search(r"github\.com", seg):
        if re.search(r"(-X|--request)\s*(POST|PATCH|PUT|DELETE)\b|(^|\s)(-d|--data\S*|-F|--form)\b", seg, re.I):
            return "curl with a write method to GitHub."
    return None


SQL_WRITE = re.compile(r"\b(drop|delete|truncate|update|insert|alter|create|grant|revoke|vacuum\s+full)\b", re.I)


def docker_refusal(seg, config):
    if not re.search(r"\bdocker\b", seg):
        return None
    if re.search(r"\bdocker\s+(system|volume|container|image|network|builder)\s+prune\b|\bdocker\s+volume\s+rm\b", seg):
        return "daemon-wide Docker prunes and volume removal can destroy other sessions' and the user's stacks."
    protected = [n for n in config.get("protectedNames", []) if n and n in seg]
    if not protected:
        return None
    if re.search(r"\bdocker\s+(stop|kill|rm|restart|pause|rename|update)\b", seg) or \
            re.search(r"\bdocker[- ]compose\b.*\b(down|rm|stop|kill|restart|up|pause|create)\b", seg):
        return (f"{', '.join(protected)} is a shared stack the profile protects. Leave it running as it is; "
                "use your own throwaway stack (see the profile's Test stack) for anything that resets or restarts.")
    if re.search(r"\bdocker\s+exec\b", seg) and SQL_WRITE.search(seg):
        return f"writing SQL into {', '.join(protected)}: the shared stack is read-only for pipeline agents."
    return None


def destructive_refusal(pattern, command, config):
    """`command` runs `pattern`, which resets whatever it points at. Refuse a shared or unnamed target."""
    ports = [p for p in config.get("sharedPorts", []) if p and re.search(r"(?<!\d)" + re.escape(p) + r"(?!\d)", command)]
    if ports:
        return (f"{pattern} changes the shared state of the stack it runs on (it resets data or widens access), and this command points at the shared "
                f"stack (port {', '.join(ports)}). Run it on your own throwaway stack (profile: Test stack).")
    markers = config.get("targetMarkers", [])
    if markers and not any(m in command for m in markers):
        return (f"{pattern} changes the shared state of the stack it runs on (it resets data or widens access), and this command does not name its target, "
                "so it would fall back to the default ports from .env. Source your throwaway stack's env file "
                "in the same command (set -a; . <env>; set +a; ...) or set one of the profile's targetMarkers explicitly.")
    return None


REDIRECT = re.compile(r"(?<![0-9&<>=\-])(?:&>>?|>>?|\|\s*tee(?:\s+-a)?)\s*([^\s;&|)]+)")
INPLACE = re.compile(r"\b(sed|perl|ruby)\b[^|;&]*\s-[a-zA-Z]*i\b|\bprettier\b[^|;&]*--write|\beslint\b[^|;&]*--fix"
                     r"|(^|[;&|]\s*)patch\s|\bnpm\s+run\s+(fix|format)")


def shell_write_refusal(command, role, cwd, roots, main_root):
    if (role in REPORT_SUFFIX or role in NAMED_FILES) and INPLACE.search(command):
        return f"{role} reports only: no in-place edits, formatters or patches. Fixes go back through implement-plan."
    for target in REDIRECT.findall(strip_heredocs(command)):
        if target[:1] in ("$", "&", "=") or target.startswith("/dev/"):
            continue
        reason = write_refusal(role, target.strip("'\""), cwd, roots, main_root)
        if reason:
            return reason
    return None


OPERATOR_CHARS = "();<>|&\n"
SEPARATORS = {"|", "||", "&&", ";", "&", "(", ")", ";;", "|&", "\n"}
WRAPPERS = {"sudo", "env", "time", "command", "nohup", "exec", "nice", "cross-env", "caffeinate", "timeout", "gtimeout"}
# Wrapper options that take a value (`caffeinate -t 600`, `nice -n 5`, `timeout -s KILL 60`), and wrappers
# whose first positional argument is theirs, not the command's (`timeout 600 npm …`).
WRAPPER_VALUE_OPTIONS = {"caffeinate": {"-t", "-w"}, "nice": {"-n"}, "timeout": {"-s", "-k"}, "gtimeout": {"-s", "-k"}}
WRAPPER_POSITIONAL = {"timeout": 1, "gtimeout": 1}
SHELLS = {"bash", "sh", "zsh"}
INTERPRETERS = {"node", "npx", "bash", "sh", "zsh"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def strip_heredocs(command):
    # Heredoc bodies are data. Keep the rest of the first line: `cat <<'EOF' > file` still redirects.
    return re.sub(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\1\b", r"\2", command, flags=re.S)


def simple_commands(command, depth=0):
    """Split a shell command into simple commands, each a token list with quotes resolved.

    Returns None when the command cannot be tokenized (unbalanced quotes); callers then fall back
    to the regex checks.
    """
    lex = shlex.shlex(strip_heredocs(command), posix=True, punctuation_chars=OPERATOR_CHARS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None
    commands, current = [], []
    for token in tokens:
        if token in SEPARATORS or (token and set(token) <= set("|;&()\n") and ">" not in token):
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    # `bash -c "<script>"`, `sh -c`, `eval "<script>"`: check the script too.
    nested = []
    for cmd in commands:
        argv = program_argv(cmd)
        if depth < 2 and argv:
            script = None
            if os.path.basename(argv[0]) in SHELLS and "-c" in argv[1:-1]:
                script = argv[argv.index("-c") + 1]
            elif argv[0] == "eval" and len(argv) > 1:
                script = " ".join(argv[1:])
            if script:
                inner = simple_commands(script, depth + 1)
                if inner is None:
                    return None
                nested.extend(inner)
    return commands + nested


def program_argv(tokens):
    """The command's argv: leading assignments, wrappers and redirections removed."""
    argv, i = [], 0
    while i < len(tokens) and (ASSIGNMENT.match(tokens[i]) or tokens[i] in WRAPPERS):
        wrapper = tokens[i]
        i += 1
        if wrapper not in WRAPPER_VALUE_OPTIONS:
            continue
        while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "--":
            i += 2 if tokens[i] in WRAPPER_VALUE_OPTIONS[wrapper] else 1
        i += WRAPPER_POSITIONAL.get(wrapper, 0)
    while i < len(tokens):
        token = tokens[i]
        if ">" in token or token in ("<", "<<", "<<<"):
            i += 2
            continue
        argv.append(token)
        i += 1
    if argv and argv[0].isdigit() and len(argv) > 1:
        argv = argv[1:]
    return argv


def redirect_targets(tokens):
    targets = []
    for i, token in enumerate(tokens):
        if ">" in token and set(token) <= set("<>&|") and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if token.endswith("&") and (nxt.isdigit() or nxt == "-"):
                continue
            targets.append(nxt)
    argv = program_argv(tokens)
    if argv and os.path.basename(argv[0]) == "tee":
        targets.extend(a for a in argv[1:] if not a.startswith("-"))
    return targets


def inplace_edit(argv):
    if not argv:
        return False
    prog = os.path.basename(argv[0])
    args = argv[1:]
    if prog in ("sed", "perl", "ruby") and any(re.match(r"^-[a-zA-Z]*i", a) for a in args):
        return True
    if prog == "patch":
        return True
    if "prettier" in [os.path.basename(a) for a in argv] and any(a in ("--write", "-w") for a in args):
        return True
    if "eslint" in [os.path.basename(a) for a in argv] and "--fix" in args:
        return True
    if prog in ("npm", "yarn", "pnpm") and len(args) >= 2 and args[0] == "run" and re.match(r"^(fix|format|lint:fix)", args[1]):
        return True
    return False


def destructive_target(argv, config):
    """The destructive pattern this command runs, if any. Matches the program, not quoted text."""
    rest = list(argv)
    while rest and os.path.basename(rest[0]) in INTERPRETERS:
        rest = rest[1:]
        while rest and rest[0].startswith("-") and rest[0] != "--":
            rest = rest[1:]
    for joined in (" ".join(argv), " ".join(rest)):
        for pattern in config.get("destructiveCommands", []):
            if pattern and joined.startswith(pattern):
                return pattern
    return None


def bash_refusal(command, role, cwd, roots, main_root, config):
    for pattern in config.get("denyAlways", []):
        if pattern and pattern in command:
            return f"'{pattern}' resets a shared stack; pipeline agents never run it."
    commands = simple_commands(command)
    if commands is None:
        return regex_bash_refusal(command, role, cwd, roots, main_root, config)
    here = cwd
    for tokens in commands:
        argv = program_argv(tokens)
        if not argv:
            continue
        prog = os.path.basename(argv[0])
        if prog == "cd" and len(argv) > 1 and not argv[1].startswith("$") and argv[1] != "-":
            # A relative path after `cd` is relative to that directory, not to the session's cwd.
            target_dir = os.path.expanduser(argv[1])
            here = os.path.realpath(target_dir if os.path.isabs(target_dir) else os.path.join(here, target_dir))
            continue
        joined = " ".join(argv)
        reason = None
        if prog == "git":
            reason = git_refusal(joined, role, here, config)
        elif prog in ("gh", "curl"):
            reason = gh_refusal(joined)
        elif prog in ("docker", "docker-compose"):
            reason = docker_refusal(joined, config)
        if reason:
            return reason
        pattern = destructive_target(argv, config)
        if pattern:
            reason = destructive_refusal(pattern, command, config)
            if reason:
                return reason
        if (role in REPORT_SUFFIX or role in NAMED_FILES) and inplace_edit(argv):
            return f"{role} reports only: no in-place edits, formatters or patches. Fixes go back through implement-plan."
        for target in redirect_targets(tokens):
            if target.startswith("/dev/") or target.startswith("$"):
                continue
            reason = write_refusal(role, target, here, roots, main_root)
            if reason:
                return reason
    return None


def regex_bash_refusal(command, role, cwd, roots, main_root, config):
    """Fallback for commands shlex cannot tokenize. Stricter: quoted text is not told apart."""
    hits = [p for p in config.get("destructiveCommands", []) if p and p in command]
    if hits:
        reason = destructive_refusal(hits[0], command, config)
        if reason:
            return reason
    for seg in SEGMENT_SPLIT.split(command):
        seg = seg.strip()
        if seg:
            reason = git_refusal(seg) or gh_refusal(seg) or docker_refusal(seg, config)
            if reason:
                return reason
    return shell_write_refusal(command, role, cwd, roots, main_root)


def redact(text):
    text = re.sub(r"((?:PASSWORD|SECRET|TOKEN|KEY)[A-Z_]*=)(\S+)", r"\1***", text, flags=re.I)
    return text[:400]


def deny(reason, payload):
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "agent_type": payload.get("agent_type"),
        "agent_id": payload.get("agent_id"),
        "tool": payload.get("tool_name"),
        "reason": reason,
        "input": redact(json.dumps(payload.get("tool_input", {}))),
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Pipeline guard: " + reason,
        }
    }))
    sys.exit(0)


def agent_role(payload):
    """The pipeline role of this call: the subagent type, or, for the main session of a scheduled
    headless run, the role its launcher set in PIPELINE_GUARD_ROLE."""
    return payload.get("agent_type") or os.environ.get("PIPELINE_GUARD_ROLE") or ""


def check(payload):
    # pipeline-<role>-artifacts is the same role with the Artifact tool added.
    role = re.sub(r"-artifacts$", "", agent_role(payload))
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    roots, main_root = repo_roots(cwd)
    config = load_config(main_root)

    if tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return write_refusal(role, path, cwd, roots, main_root) if path else None

    if tool == "Bash":
        return bash_refusal(tool_input.get("command") or "", role, cwd, roots, main_root, config)
    return None


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        sys.exit(0)
    role = agent_role(payload)
    if not role.startswith("pipeline-"):
        sys.exit(0)
    try:
        reason = check(payload)
    except Exception as exc:  # a broken guard must not wave a pipeline agent through
        reason = f"the guard itself failed ({type(exc).__name__}: {exc}); refusing to be safe."
    if reason:
        deny(reason, payload)
    sys.exit(0)


if __name__ == "__main__":
    main()
