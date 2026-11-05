#!/usr/bin/env python3
"""Triage the pull requests merged into the base branch since the last repo pulse. No tokens: git only,
plus one read-only gh call for authors and labels.

Usage: prscan.py [--since YYYY-MM-DD] [--base origin/main] [--cwd <dir in the repository>]

Without --since it starts after the commit recorded in <work>/repo-pulse-state.json, or 7 days back.
Prints one JSON object: the range, and one row per merged PR (squash commit) with its changed files,
the signals that make it worth reading, the memory or profile files that name a path it touched, and a
class: high (read it), medium (skim title and files), low (count only).

The patterns (keys strong, strongAdded, weak) and the base branch can be overridden by a ```json block
under "## Repo pulse" in the profile.
"""

import argparse
import datetime
import glob
import json
import os
import re
import subprocess

# Strong signals make a PR "high": it changes how the project works for everyone. Weak ones make it
# "medium". A signal is a path pattern, optionally limited to files the PR added (status A).
STRONG = {
    "instructions": r"(^|/)(CLAUDE|AGENTS)\.md$",
    "runtime": r"(^|/)\.nvmrc$|(^|/)\.node-version$|(^|/)\.tool-versions$",
    "build-config": r"(^|/)(vite|jest|vitest|babel|webpack)\.config\.[cm]?[jt]s$|(^|/)eslint\.config\.[cm]?js$|(^|/)\.eslintrc",
}
STRONG_ADDED = {
    "new-migration": r"(^|/)migrations/",
    "new-guard-test": r"(?i)__tests__/[^/]*(guard|invariant|contract|compat|policy|regression)[^/]*$",
}
WEAK = {
    "docs": r"^docs/",
    "ci": r"^\.github/workflows/",
    "platform": r"(^|/)charts/|(^|/)Dockerfile|(^|/)docker-compose",
    "config": r"(?i)(^|/)config/|(^|/)\.env\.example$",
    "guard-test": r"(?i)__tests__/[^/]*(guard|invariant|contract|compat|policy|regression)[^/]*$",
}
# package.json changes count only when a changed line touches one of these keys, or a dependency's
# major version moves.
PACKAGE_KEYS = r'^[+-]\s*"(engines|node|type|packageManager|test[^"]*|build[^"]*|lint[^"]*|start[^"]*|migrate[^"]*)"\s*:'
# A path memory names counts for the staleness list only if at most this many memory files name it.
DISTINCTIVE = 3
# File names too common to tie a change to one note, wherever they sit.
GENERIC_NAMES = {"readme.md", "package.json", "package-lock.json", "claude.md", "agents.md", "index.js",
                 "index.ts", "changelog.md", "tsconfig.json"}
SKIP_SUBJECT = r"^(Bump |Merge branch )"
SRC_HINT = r"(^|/)(src|lib|scripts|test)/"


def run(cmd, cwd):
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    return out.stdout if out.returncode == 0 else ""


def main_checkout(cwd):
    common = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd).strip()
    return os.path.dirname(common) if common else os.path.realpath(cwd)


def profile_text(root):
    try:
        with open(os.path.join(root, ".claude", "pipeline.local.md"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def section(text, title):
    m = re.search(r"^## " + re.escape(title) + r"\b(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def work_dir(root, text):
    m = re.search(r"`([^`]+/)`", section(text, "Work directory"))
    return os.path.join(root, m.group(1)) if m else None


def memory_files(root):
    mem = os.path.expanduser("~/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", root) + "/memory")
    return sorted(glob.glob(os.path.join(mem, "*.md")))


PATH_TOKEN = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:js|mjs|cjs|ts|tsx|jsx|json|ya?ml|md|sh|py|sql|adoc|mts))(?![\w/-])")


def reference_index(root, text):
    """path token -> files (memory, profile) that name it. Bare file names count only when distinctive."""
    index = {}
    sources = [(f, open(f, encoding="utf-8").read()) for f in memory_files(root)]
    sources.append((os.path.join(root, ".claude", "pipeline.local.md"), text))
    for name, body in sources:
        for token in set(PATH_TOKEN.findall(body)):
            base = os.path.basename(token)
            if base.lower() in GENERIC_NAMES or ("/" not in token and len(base) < 12):
                continue
            index.setdefault(token, set()).add(os.path.basename(name))
    return index


def refs_for(paths, index, distinctive=DISTINCTIVE):
    """Memory or profile files naming one of these paths. With `distinctive`, a path named by more files
    than that is skipped: it is everywhere (an entry point), so a change to it says nothing about one note."""
    hits = set()
    for path in paths:
        for token, names in index.items():
            if distinctive and len(names) > distinctive:
                continue
            if path == token or path.endswith("/" + token) or ("/" not in token and os.path.basename(path) == token):
                hits.update(names)
    return sorted(hits)


def package_changes(sha, files, root):
    """Changed package.json lines that matter: runtime, module type, scripts people run, and dependency
    major-version moves."""
    found = []
    for f in files:
        diff = run(["git", "show", "--format=", "--unified=0", sha, "--", f], root)
        minus, plus = {}, {}
        for line in diff.splitlines():
            if line.startswith(("+++", "---")):
                continue
            if re.match(PACKAGE_KEYS, line):
                found.append(f + ": " + line.strip()[:120])
            dep = re.match(r'^([+-])\s*"([@\w./-]+)"\s*:\s*"[\^~>=]*(\d+)\.', line)
            if dep:
                (plus if dep.group(1) == "+" else minus)[dep.group(2)] = int(dep.group(3))
        for name, major in plus.items():
            if name in minus and minus[name] != major:
                found.append(f"{f}: {name} major {minus[name]} -> {major}")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--full", action="store_true", help="every field for every PR")
    a = ap.parse_args()

    root = main_checkout(a.cwd)
    text = profile_text(root)
    config = {}
    block = re.search(r"```json\s*\n(.*?)\n```", section(text, "Repo pulse"), re.S)
    if block:
        config = json.loads(block.group(1))
    strong = dict(STRONG, **config.get("strong", {}))
    strong_added = dict(STRONG_ADDED, **config.get("strongAdded", {}))
    weak = dict(WEAK, **config.get("weak", {}))
    base = config.get("base", a.base)

    work = work_dir(root, text)
    state_path = os.path.join(work, "repo-pulse-state.json") if work else None
    state = {}
    if state_path and os.path.exists(state_path):
        state = json.load(open(state_path))

    if a.since:
        rng = ["--since", a.since, base]
        since = a.since
    elif state.get("lastCommit") and run(["git", "cat-file", "-t", state["lastCommit"]], root).strip() == "commit":
        rng = [f"{state['lastCommit']}..{base}"]
        since = state.get("lastRunAt", state["lastCommit"])
    else:
        since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        rng = ["--since", since, base]

    log = run(["git", "log", "--first-parent", "--format=%H%x1f%an%x1f%ad%x1f%s", "--date=iso-strict"] + rng, root)
    commits = [line.split("\x1f") for line in log.splitlines() if line.count("\x1f") == 3]

    gh_meta = {}
    remote = run(["git", "remote", "get-url", "origin"], root).strip()
    repo = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote)
    if repo and commits:
        oldest = commits[-1][2][:10]
        listing = run(["gh", "pr", "list", "--repo", repo.group(1), "--state", "merged", "--limit", "500",
                       "--search", f"merged:>={oldest}", "--json", "number,author,labels"], root)
        try:
            for pr in json.loads(listing or "[]"):
                gh_meta[pr["number"]] = {"author": (pr.get("author") or {}).get("login"),
                                         "labels": [l["name"] for l in pr.get("labels") or []]}
        except ValueError:
            pass

    index = reference_index(root, text)
    rows = []
    for sha, author, date, subject in commits:
        status = run(["git", "show", "--name-status", "--format=", sha], root)
        numstat = run(["git", "show", "--numstat", "--format=", sha], root)
        changed = []
        for line in status.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                changed.append((parts[0][0], parts[-1], parts[1] if parts[0].startswith("R") else None))
        lines = sum(int(p[0]) + int(p[1]) for p in (l.split("\t") for l in numstat.splitlines())
                    if len(p) >= 3 and p[0].isdigit() and p[1].isdigit())
        paths = [p for _, p, _ in changed]
        added = [p for s, p, _ in changed if s == "A"]
        hit = lambda patterns, pool: sorted(n for n, pat in patterns.items() if any(re.search(pat, p) for p in pool))
        found = hit(strong, paths) + hit(strong_added, added)
        package_lines = package_changes(sha, [p for p in paths if p.endswith("package.json")], root)
        if package_lines:
            found.append("package")
        removed = [old or p for s, p, old in changed if s in ("D", "R") and re.search(SRC_HINT, old or p)
                   and "__tests__" not in (old or p)]
        if removed:
            found.append("removal")
        weak_found = [w for w in hit(weak, paths) if w not in found]
        gone = [old or p for s, p, old in changed if s in ("D", "R")]
        broken = refs_for(gone, index, distinctive=None)
        if broken:
            found.append("memory-path-gone")
        signal_files = [p for p in paths if any(re.search(pat, p) for pat in
                        list(strong.values()) + list(strong_added.values()))]
        refs = refs_for(signal_files + removed, index)
        pr = re.search(r"\(#(\d+)\)\s*$", subject)
        number = int(pr.group(1)) if pr else None
        if re.search(SKIP_SUBJECT, subject):
            klass = "low"
        elif found:
            klass = "high"
        elif weak_found or (lines >= 50 and any(re.search(SRC_HINT, p) for p in paths)):
            klass = "medium"
        else:
            klass = "low"
        meta = gh_meta.get(number, {})
        rows.append({
            "pr": number, "sha": sha[:10], "mergedAt": date, "subject": subject,
            "ticket": (re.match(r"#(\d+)", subject) or [None, None])[1],
            "author": meta.get("author") or author, "labels": meta.get("labels", []),
            "files": len(paths), "lines": lines, "class": klass, "signals": found, "weakSignals": weak_found,
            "memoryRefs": refs, "memoryPathGone": broken, "removed": removed[:20], "packageLines": package_lines[:12],
            "signalFiles": signal_files[:25],
        })

    counts = {k: sum(1 for r in rows if r["class"] == k) for k in ("high", "medium", "low")}
    # Notes to check for staleness: those naming a path a high PR changed, and those naming a path
    # any PR deleted or renamed.
    stale = {}
    for r in rows:
        for note in r["memoryPathGone"]:
            stale.setdefault(note, {"pathGone": [], "highPrs": []})["pathGone"].append(r["pr"])
        if r["class"] == "high":
            for note in r["memoryRefs"]:
                stale.setdefault(note, {"pathGone": [], "highPrs": []})["highPrs"].append(r["pr"])
    if not a.full:
        brief = []
        for r in rows:
            if r["class"] == "high":
                brief.append({k: v for k, v in r.items() if k != "memoryRefs"})
            elif r["class"] == "medium":
                brief.append({k: r[k] for k in ("pr", "sha", "subject", "author", "files", "lines", "class", "weakSignals")})
            else:
                brief.append({k: r[k] for k in ("pr", "subject", "class")})
        rows = brief
    print(json.dumps({
        "repo": os.path.basename(root), "base": base, "since": since,
        "headCommit": run(["git", "rev-parse", base], root).strip(),
        "statePath": state_path, "counts": counts, "notesToCheck": stale, "prs": rows,
    }, indent=1))


if __name__ == "__main__":
    main()
