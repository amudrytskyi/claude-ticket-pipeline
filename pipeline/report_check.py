#!/usr/bin/env python3
"""SubagentStop check for ticket-pipeline report agents.

Runs when a subagent is about to stop, and acts only for the pipeline roles that end in a report:
plan-reviewer, code-reviewer, verifier, pr-feedback and retro. It checks that the agent wrote its report
during this run and that the report's latest section has the lines the skill requires. If not, it
blocks the stop once, and the reason becomes the agent's next instruction. A second stop is never
blocked (stop_hook_active), so an agent that stopped on purpose (missing input, needsArtifactTool, a
blocker) says so and finishes.

Every block is appended to ~/.claude/pipeline/guard.log as one JSON line with event "report-check".
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time

LOG_PATH = os.environ.get("PIPELINE_GUARD_LOG") or os.path.expanduser("~/.claude/pipeline/guard.log")

# role -> (report suffix, heading of a section, required patterns in the latest section)
CHECKS = {
    "pipeline-plan-reviewer": ("-plan-review.md", r"^## Round \d+", [
        (r"Expectations", "the round's Expectations"),
        (r"\bBase\b", "the Base line"),
        (r"Counts: new C/H/M/L \d+/\d+/\d+/\d+; open C/H/M/L \d+/\d+/\d+/\d+",
         "the count line `Counts: new C/H/M/L n/n/n/n; open C/H/M/L n/n/n/n`"),
        (r"Verdict: (READY|NOT READY)\b", "the verdict line `Verdict: READY` or `Verdict: NOT READY`"),
    ]),
    "pipeline-code-reviewer": ("-code-review.md", r"^## Round \d+", [
        (r"\bHEAD\b", "the HEAD you reviewed"),
        (r"^\|.*\|", "the criteria table"),
        (r"Verdict: (ALL ACS CLOSED|GAPS)\b", "the verdict line `Verdict: ALL ACS CLOSED` or `Verdict: GAPS`"),
    ]),
    "pipeline-verifier": ("-verify.md", r"^## Run \d+", [
        (r"\bHEAD\b", "the HEAD you verified"),
        (r"^\|.*\|", "the table of steps"),
        (r"Verdict: (GREEN|RED|BLOCKED)\b", "the verdict line `Verdict: GREEN`, `Verdict: RED` or `Verdict: BLOCKED`"),
    ]),
    "pipeline-pr-feedback": ("-pr-feedback.md", r"^## Round \d+", [
        (r"^\|.*\|", "the table of items"),
        (r"Verdict: (FIXES NEEDED|PLAN CHANGE NEEDED|NOTHING TO FIX)\b",
         "the verdict line `Verdict: FIXES NEEDED`, `Verdict: PLAN CHANGE NEEDED` or `Verdict: NOTHING TO FIX`"),
    ]),
}


def first_prompt(transcript):
    """Text of the subagent's first user message (its task), or ""."""
    try:
        with open(os.path.expanduser(transcript), encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") == "user":
                    content = (entry.get("message") or {}).get("content")
                    if isinstance(content, list):
                        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    return content or ""
    except (OSError, TypeError):
        pass
    return ""


def started_at(transcript):
    """Epoch seconds of the subagent's first transcript entry, or None."""
    try:
        with open(os.path.expanduser(transcript), encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line).get("timestamp")
                except ValueError:
                    continue
                if value:
                    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (OSError, TypeError):
        pass
    return None


def repo_root(cwd):
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def work_dir(root):
    """The work directory the profile names: the first backticked path under "## Work directory"."""
    for base in (root, main_checkout(root)):
        try:
            with open(os.path.join(base, ".claude", "pipeline.local.md"), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        section = re.search(r"^## Work directory\b(.*?)(?=^## |\Z)", text, re.S | re.M)
        path = re.search(r"`([^`]+/)`", section.group(1)) if section else None
        if path:
            return os.path.join(root, path.group(1))
    return None


def main_checkout(root):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=5)
        return os.path.dirname(out.stdout.strip()) if out.returncode == 0 else root
    except Exception:
        return root


def report_files(root, suffix):
    """Report files in the profile's work directory; without one, untracked or ignored files in the
    checkout whose name ends with the suffix (slower: it walks ignored directories)."""
    work = work_dir(root)
    if work and os.path.isdir(work):
        return [os.path.join(work, n) for n in os.listdir(work) if n.endswith(suffix)]
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "--ignored", "--",
             f":(glob)**/*{suffix}"], capture_output=True, text=True, timeout=20)
        names = out.stdout.split() if out.returncode == 0 else []
        out2 = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "--", f":(glob)**/*{suffix}"],
            capture_output=True, text=True, timeout=20)
        names += out2.stdout.split() if out2.returncode == 0 else []
    except Exception:
        return []
    return sorted({os.path.join(root, n) for n in names if "node_modules/" not in n})


def latest_section(text, heading):
    starts = [m.start() for m in re.finditer(heading, text, re.M)]
    return text[starts[-1]:] if starts else ""


def problem(payload):
    role = re.sub(r"-artifacts$", "", payload.get("agent_type") or "")
    since = started_at(payload.get("agent_transcript_path")) or (time.time() - 6 * 3600)
    if role == "pipeline-retro":
        if "digest" in first_prompt(payload.get("agent_transcript_path")).split("SKILL.md")[-1][:400]:
            return None
        work = work_dir(repo_root(payload.get("cwd") or os.getcwd()) or "")
        runs = os.path.join(work, "pipeline-runs.jsonl") if work else None
        if not runs or not os.path.exists(runs) or os.path.getmtime(runs) < since:
            return "you did not append this run's line to pipeline-runs.jsonl in the work directory"
        try:
            with open(runs, encoding="utf-8") as fh:
                last = [l for l in fh if l.strip()][-1]
            if "ticket" not in json.loads(last):
                return "the last line of pipeline-runs.jsonl has no ticket field"
        except (ValueError, IndexError):
            return "the last line of pipeline-runs.jsonl is not valid JSON"
        return None
    if role == "pipeline-learner":
        work = work_dir(repo_root(payload.get("cwd") or os.getcwd()) or "")
        state = os.path.join(work, "repo-pulse-state.json") if work else None
        knowledge = os.path.join(work, "repo-knowledge.md") if work else None
        if not state or not os.path.exists(state) or os.path.getmtime(state) < since:
            return "you did not update repo-pulse-state.json in the work directory"
        if not os.path.exists(knowledge) or os.path.getmtime(knowledge) < since:
            return "you did not update repo-knowledge.md in the work directory"
        return None
    if role not in CHECKS:
        return None
    suffix, heading, required = CHECKS[role]
    root = repo_root(payload.get("cwd") or os.getcwd())
    if not root:
        return None
    fresh = [f for f in report_files(root, suffix) if os.path.getmtime(f) >= since - 5]
    if not fresh:
        return f"you did not write your report (a *{suffix} file in the work directory) in this run"
    # Several tickets can be in flight: prefer the report whose ticket the agent's task names.
    task = first_prompt(payload.get("agent_transcript_path"))
    named = [f for f in fresh if re.search(r"(?<![0-9A-Za-z])#?" + re.escape(os.path.basename(f)[:-len(suffix)]) + r"(?![0-9])", task)]
    path = max(named or fresh, key=os.path.getmtime)
    with open(path, encoding="utf-8") as fh:
        section = latest_section(fh.read(), heading)
    if not section:
        return f"{path} has no section starting with a `{heading.strip('^').replace(chr(92) + 'd+', 'N')}` heading"
    missing = [label for pattern, label in required if not re.search(pattern, section, re.M)]
    if missing:
        return f"the latest section of {path} is missing " + "; ".join(missing)
    return None


def log(payload, reason):
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": "report-check",
        "agent_type": payload.get("agent_type"),
        "agent_id": payload.get("agent_id"),
        "reason": reason,
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        sys.exit(0)
    if not (payload.get("agent_type") or "").startswith("pipeline-") or payload.get("stop_hook_active"):
        sys.exit(0)
    try:
        reason = problem(payload)
    except Exception:
        reason = None  # a broken check must not trap an agent; the guard covers safety
    if reason:
        log(payload, reason)
        print(json.dumps({
            "decision": "block",
            "reason": ("Pipeline report check: " + reason + ". Finish the report as the skill says, then end "
                       "with the structured result your task asks for. If you stopped on purpose before "
                       "writing it (missing input, needsArtifactTool, a blocker), reply with "
                       "STOPPED-WITHOUT-REPORT: <why> and the same structured result."),
        }))
    sys.exit(0)


if __name__ == "__main__":
    main()
