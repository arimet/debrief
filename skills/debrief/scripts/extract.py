"""Assemble one week of a repository's activity into facts.json and digest files."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path, PurePosixPath

import gitfacts
import prfacts
import sessions as sess

DIFF_MAX_LINES = 2000
SCORES = {"blind_spot": 3, "untested": 2, "migration": 2, "dependency": 2, "hotzone": 1}


def week_folder(repo: Path, until: date) -> Path:
    year, week, _ = until.isocalendar()
    return Path(repo) / ".debrief" / f"{year}-W{week:02d}"


def ensure_excluded(repo: Path) -> None:
    common = Path(gitfacts.git(repo, "rev-parse", "--git-common-dir").strip())
    exclude = (common if common.is_absolute() else Path(repo) / common) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    if ".debrief/" not in lines:
        exclude.write_text("".join(l + "\n" for l in lines) + ".debrief/\n", encoding="utf-8")


def _ref(repo: Path, branch: str) -> str:
    for ref in (f"origin/{branch}", branch):
        try:
            gitfacts.git(repo, "rev-parse", "--verify", "-q", ref)
            return ref
        except subprocess.CalledProcessError:
            continue
    return "HEAD"


def _is_spec(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    return bool({"docs", "adr"} & set(p.parts[:-1])) or name.endswith(".adr.md") or (
        "spec" in name and name.endswith(".md"))


def priorities(facts: dict, exists=None) -> list:
    """Every changed file that still exists, scored. `exists` defaults to the files present at the analyzed ref."""
    changed = {f["path"] for c in facts["commits"] for f in c["files"]}
    exists = changed if exists is None else exists
    sets = {"blind_spot": {b["path"] for b in facts["blind_spots"]},
            "untested": set(facts["risks"]["untested"]), "migration": set(facts["risks"]["migrations"]),
            "dependency": set(facts["risks"]["dependencies"]),
            "hotzone": {h["path"] for h in facts["hotzones"]["files"]}}
    rows = []
    for path in changed & set(exists):
        reasons = [r for r in SCORES if path in sets[r]]
        rows.append({"path": path, "score": sum(SCORES[r] for r in reasons), "reasons": reasons})
    return sorted(rows, key=lambda r: (-r["score"], r["path"]))


def _config(repo: Path, key: str) -> str:
    try:
        return gitfacts.git(repo, "config", key).strip()
    except subprocess.CalledProcessError:
        return ""


def build(repo: Path, since: date, until: date, projects_dir, run=subprocess.run, branch: str = None) -> dict:
    repo = Path(repo).resolve()
    branch = branch or gitfacts.default_branch(repo)
    ref = _ref(repo, branch)
    commits = gitfacts.commits(repo, since, until, ref)
    host = prfacts.detect_host(repo)
    prs, warnings = prfacts.merged_prs(repo, since, until, host, run=run, base=branch)
    # with a host and no failed fetch, zero PRs is real data
    prs_known = host is not None and not any("PR data skipped" in w for w in warnings)
    if gitfacts.git(repo, "rev-parse", "HEAD").strip() != gitfacts.git(repo, "rev-parse", ref).strip():
        warnings.append(f"your checkout (HEAD) differs from {ref}: quiz file:line refs point to {ref}")
    if host is None:
        warnings.append("no PR host detected: PR data skipped")
    user = {"name": _config(repo, "user.name"), "email": _config(repo, "user.email")}
    tracked = gitfacts.tracked(repo, ref)
    blind = gitfacts.blind_spots(repo, commits, user["email"], user["name"], ref)
    if not user["email"]:
        warnings.append("git user.email is empty: every changed file is treated as a blind spot")
        authors = {}
        for c in commits:
            for f in c["files"]:
                authors.setdefault(f["path"], [])
                if c["author"] not in authors[f["path"]]:
                    authors[f["path"]].append(c["author"])
        blind = [{"path": p, "authors": a} for p, a in authors.items() if p in tracked]
    hot = gitfacts.hotzones(commits)
    hot["files"] = [h for h in hot["files"] if h["path"] in tracked]
    found = sess.find_sessions(sess.worktree_roots(repo), since.isoformat(), until.isoformat(), projects_dir)
    facts = {
        "period": {"since": since.isoformat(), "until": until.isoformat(),
                   "week": week_folder(repo, until).name},
        "repo": {"name": repo.name, "root": str(repo), "default_branch": branch, "ref": ref, "host": host},
        "user": user,
        "sources": {"git": True, "prs": prs_known, "sessions": len(found), "specs": []},
        "commits": commits,
        "hotzones": hot,
        "risks": gitfacts.risks(commits, repo, ref),
        "blind_spots": blind,
        "prs": prs,
        "repo_signals": gitfacts.commit_signals(commits) + prfacts.pr_signals(prs),
        "sessions": [],
        "digests": {"features": [], "sessions": [], "unlinked": []},
        "warnings": warnings,
    }
    facts["sources"]["specs"] = sorted({f["path"] for c in commits for f in c["files"]
                                        if f["path"] in tracked and _is_spec(f["path"])})
    facts["priorities"] = priorities(facts, tracked)
    facts["_session_paths"] = found  # private: write_week pops it so sessions are searched once
    return facts


DEPS_TITLE = re.compile(r"^(chore|build)\(deps[^)]*\)|^bump |dependabot", re.I)
RELEASE_TITLE = re.compile(r"^v?\d+\.\d+\.\d+$|^release|^chore\(release\)", re.I)
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "go.sum",
             "composer.lock", "Gemfile.lock"}
VERSION_FILES = {"VERSION", "version.txt", ".version", ".bumpversion.cfg"}


def _is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return (name in LOCKFILES or name in gitfacts.DEP_NAMES or name in VERSION_FILES
            or (name.startswith("requirements") and name.endswith(".txt")))


def _is_changelog(path: str) -> bool:
    return PurePosixPath(path).name.upper().startswith("CHANGELOG")


def _is_ci(path: str) -> bool:
    return path.startswith((".github/", ".circleci/")) or path == ".gitlab-ci.yml"


def is_trivial(pr: dict) -> bool:
    """True when a PR needs no AI summary. A dependency PR (deps/bump/dependabot title, bot author) is trivial only
    if it touches nothing but manifests and lockfiles; a release/version PR only if it touches manifests,
    lockfiles and changelogs; any other PR only if it is all CI config or all tests. A bump that also changes
    source files is not trivial."""
    files = pr["files"]
    if not files:
        return False

    def all_(check):
        return all(check(f) for f in files)

    if DEPS_TITLE.search(pr["title"]) or pr.get("author", "").endswith("[bot]"):
        return all_(_is_manifest)
    if RELEASE_TITLE.search(pr["title"]):
        return all_(lambda f: _is_manifest(f) or _is_changelog(f))
    return all_(_is_manifest) or all_(_is_ci) or all_(gitfacts.is_test)


def trivial_digests(prs: list) -> list:
    return [f"digests/feature-{pr['number']}.md" for pr in prs if is_trivial(pr)]


def _has_commit(repo: Path, sha: str) -> bool:
    try:
        gitfacts.git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


def _pr_diff(repo: Path, pr: dict) -> str:
    shas, files, merge = pr["commits"], pr["files"], pr.get("merge_commit")
    if merge and not (shas and _has_commit(repo, shas[0]) and _has_commit(repo, shas[-1])):
        # squash or rebase merge: the branch commits are gone, the merge commit holds the change
        try:
            return gitfacts.git(repo, "diff", f"{merge}^1", merge, "--", *files)
        except subprocess.CalledProcessError:
            return ""
    if not shas:
        return ""
    try:
        return gitfacts.git(repo, "diff", f"{shas[0]}^..{shas[-1]}", "--", *files)
    except subprocess.CalledProcessError:
        parts = []
        for sha in shas:
            try:
                parts.append(gitfacts.git(repo, "show", sha, "--format=", "--", *files))
            except subprocess.CalledProcessError:
                continue
        return "".join(parts)


def _weakened_signal(pr: dict, diff: str):
    lost = gitfacts.weakened_tests(diff)
    if not lost:
        return None
    ref = f"#{pr['number']}"
    return {"type": "tests_weakened", "ref": ref,
            "note": f"{ref} removed {lost['assertions']} assertion(s) and {lost['cases']} test case(s) "
                    "from test files while changing the code"}


def _feature_digest(repo: Path, pr: dict, warnings: list, diff: str = None) -> str:
    lines = (_pr_diff(repo, pr) if diff is None else diff).splitlines()
    if not lines:
        warnings.append(f"PR #{pr['number']}: diff unavailable locally")
    diff = "\n".join(lines[:DIFF_MAX_LINES])
    if len(lines) > DIFF_MAX_LINES:
        diff += "\n[diff truncated]"
    files = "\n".join(f"- {p}" for p in pr["files"])
    return (f"# PR #{pr['number']}: {pr['title']}\n\n{pr['url']}\nMerged: {pr['merged_at']}\n\n"
            f"## Description\n\n{pr['body']}\n\n## Files\n\n{files}\n\n## Diff\n\n{diff}\n")


def _commit_block(c: dict) -> str:
    files = ", ".join(f["path"] for f in c["files"])
    return f"## {c['sha'][:7]} {c['subject']}\n\n{c['author']}, {c['date']}\n\n{c['body']}\n\nFiles: {files}\n"


def _unlinked_digests(unlinked: list) -> list:
    """Group commit blocks into texts of at most DIFF_MAX_LINES lines. A commit is never split across
    texts unless it alone exceeds the cap; then it gets its own text, cut and marked."""
    header = "# Commits outside any pull request\n"
    room = DIFF_MAX_LINES - len(header.splitlines()) - 1
    texts, current, size = [], [], 0
    for block in map(_commit_block, unlinked):
        lines = block.splitlines()
        if len(lines) > room:
            block = "\n".join(lines[:room - 1] + ["[diff truncated]"]) + "\n"
            lines = block.splitlines()
        if current and size + len(lines) + 1 > room:
            texts.append(header + "\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(lines) + 1
    if current:
        texts.append(header + "\n".join(current))
    return texts


def _drop_stale_summaries(week: Path, old: dict) -> None:
    """Delete summaries whose digest is gone or changed since the previous run; keep the others."""
    new = {f.stem: f.read_text(encoding="utf-8") for f in (week / "digests").glob("*") if f.is_file()}
    for f in (week / "summaries").glob("*"):
        stem = f.name.split(".")[0]
        if stem not in new or old.get(stem) != new[stem]:
            f.unlink()


def write_week(repo: Path, week: Path, facts: dict, projects_dir) -> None:
    """Write facts.json and digests. Rewrites digests/, drops summaries of changed digests and content.json, never results.json."""
    repo = Path(repo).resolve()
    week = Path(week)
    old = {f.stem: f.read_text(encoding="utf-8") for f in (week / "digests").glob("*") if f.is_file()}
    shutil.rmtree(week / "digests", ignore_errors=True)
    (week / "content.json").unlink(missing_ok=True)
    (week / "digests").mkdir(parents=True)
    session_paths = facts.pop("_session_paths", None)
    if session_paths is None:
        session_paths = sess.find_sessions(sess.worktree_roots(repo), facts["period"]["since"],
                                           facts["period"]["until"], projects_dir)

    def put(rel: str, text: str) -> str:
        (week / rel).write_text(text, encoding="utf-8")
        return rel

    for pr in facts["prs"]:
        diff = _pr_diff(repo, pr)
        signal = _weakened_signal(pr, diff)
        if signal:
            facts["repo_signals"].append(signal)
        facts["digests"]["features"].append(put(f"digests/feature-{pr['number']}.md",
                                                _feature_digest(repo, pr, facts["warnings"], diff)))
    linked = {s for pr in facts["prs"] for s in pr["commits"] + [pr.get("merge_commit")]}
    unlinked = [c for c in facts["commits"] if c["sha"] not in linked]
    facts["digests"]["unlinked"] = [put(f"digests/commits-unlinked-{k}.md", text)
                                    for k, text in enumerate(_unlinked_digests(unlinked), 1)]
    for path in session_paths:
        meta, text = sess.session_digest(path)
        rel = put(f"digests/session-{meta['id']}.txt", text)
        facts["sessions"].append({**meta, "digest": rel})
        if meta["truncated"]:
            facts["warnings"].append(f'session "{meta["title"]}" digest truncated: kept its start and end')
        facts["digests"]["sessions"].append(rel)
    facts["digests"]["trivial"] = trivial_digests(facts["prs"])
    _drop_stale_summaries(week, old)
    put("facts.json", json.dumps(facts, ensure_ascii=False, indent=2) + "\n")


def default_period(since, until, today: date):
    """Current ISO week (Monday to today); on a Monday, the previous full week.

    One given bound: until defaults to today, since to the Monday of until's week."""
    if since is None and until is None and today.weekday() == 0:
        until = today - timedelta(days=1)
    until = until or today
    return since or until - timedelta(days=until.weekday()), until


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Collect one week of repository facts.")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--projects-dir")
    ap.add_argument("--branch", help="branch to analyze (default: the repository's default branch)")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        gitfacts.git(repo, "rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, OSError):
        print(f"{repo} is not a git repository", file=sys.stderr)
        return 2
    since, until = default_period(date.fromisoformat(args.since) if args.since else None,
                                  date.fromisoformat(args.until) if args.until else None, date.today())
    facts = build(repo, since, until, args.projects_dir, branch=args.branch)
    week = week_folder(repo, until)
    ensure_excluded(repo)
    write_week(repo, week, facts, args.projects_dir)
    print(week)
    return 0


if __name__ == "__main__":
    sys.exit(main())
