#!/usr/bin/env python3
"""Per-phase numbers for one ticket's pipeline agents, read from Claude Code transcripts.

Usage: runstats.py <ticket> [--cwd <dir in the repository>] [--since YYYY-MM-DD]

Finds every subagent transcript in this repository's project directory that ran as a pipeline-*
agent type or was told to follow a pipeline skill, and whose task names the ticket, and prints one JSON object: one row per agent (type, skill, start, end,
minutes, turns, tool calls, first and peak context, token sums) plus totals, and the guard refusals
and report-check blocks those agents hit. Token sums take the largest usage seen per message id,
because a transcript repeats a message once per content block.
"""

import argparse
import datetime
import glob
import json
import os
import re
import subprocess

GUARD_LOG = os.environ.get("PIPELINE_GUARD_LOG") or os.path.expanduser("~/.claude/pipeline/guard.log")
PIPELINE_SKILLS = ("plan-ticket", "review-plan", "implement-plan", "review-code", "verify-ticket", "pr-feedback", "pipeline-retro")


def main_checkout(cwd):
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.dirname(out.stdout.strip())
    except Exception:
        pass
    return os.path.realpath(cwd)


def project_dir(checkout):
    return os.path.expanduser("~/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", checkout))


def ts(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def first_prompt(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def agent_row(path, ticket_re):
    meta_path = path[:-len(".jsonl")] + ".meta.json"
    agent_type = None
    if os.path.exists(meta_path):
        try:
            agent_type = json.load(open(meta_path)).get("agentType")
        except ValueError:
            pass
    prompt, usage, tools, times = None, {}, 0, []
    for line in open(path, encoding="utf-8"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("timestamp"):
            times.append(ts(entry["timestamp"]))
        if prompt is None and entry.get("type") == "user":
            prompt = first_prompt(entry)
        message = entry.get("message") or {}
        if entry.get("type") == "assistant" and isinstance(message, dict):
            for c in message.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tools += 1
            u = message.get("usage")
            if u:
                mid = message.get("id")
                prev = usage.get(mid)
                cur = {k: u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens", "output_tokens")}
                usage[mid] = cur if prev is None else {k: max(prev[k], cur[k]) for k in cur}
    skill = re.search(r"skills/(%s)/SKILL\.md" % "|".join(PIPELINE_SKILLS), prompt or "")
    if not (agent_type or "").startswith("pipeline-") and not skill:
        return None
    if not prompt or not ticket_re.search(prompt) or not usage:
        return None
    contexts = [u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
                for u in usage.values()]
    return {
        "agentType": agent_type,
        "agentId": os.path.basename(path)[len("agent-"):-len(".jsonl")],
        "skill": skill.group(1) if skill else None,
        "start": min(times).isoformat() if times else None,
        "end": max(times).isoformat() if times else None,
        "minutes": round((max(times) - min(times)).total_seconds() / 60, 1) if times else None,
        "turns": len(usage),
        "toolCalls": tools,
        "firstContext": contexts[0],
        "peakContext": max(contexts),
        "cacheRead": sum(u["cache_read_input_tokens"] for u in usage.values()),
        "cacheWrite": sum(u["cache_creation_input_tokens"] for u in usage.values()),
        "input": sum(u["input_tokens"] for u in usage.values()),
        "output": sum(u["output_tokens"] for u in usage.values()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticket")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--since")
    a = ap.parse_args()
    number = re.sub(r"\D", "", a.ticket) or a.ticket
    ticket_re = re.compile(r"(?<![0-9A-Za-z])#?" + re.escape(number) + r"(?![0-9])")
    since = datetime.datetime.fromisoformat(a.since).replace(tzinfo=datetime.timezone.utc) if a.since else None

    checkout = main_checkout(a.cwd)
    rows = []
    for path in glob.glob(os.path.join(project_dir(checkout), "**", "subagents", "agent-*.jsonl"), recursive=True):
        row = agent_row(path, ticket_re)
        if row and (not since or (row["start"] and ts(row["start"]) >= since)):
            rows.append(row)
    rows.sort(key=lambda r: r["start"] or "")

    ids = {r["agentId"] for r in rows}
    refusals = []
    if os.path.exists(GUARD_LOG):
        for line in open(GUARD_LOG, encoding="utf-8"):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("agent_id") in ids:
                refusals.append({k: entry.get(k) for k in ("ts", "event", "agent_type", "tool", "reason")})

    totals = {k: sum(r[k] for r in rows) for k in ("minutes", "turns", "toolCalls", "cacheRead", "cacheWrite", "input", "output")}
    totals["minutes"] = round(totals["minutes"], 1)
    totals["agents"] = len(rows)
    print(json.dumps({"ticket": a.ticket, "checkout": os.path.basename(checkout), "agents": rows,
                      "totals": totals, "guardEvents": refusals}, indent=1))


if __name__ == "__main__":
    main()
