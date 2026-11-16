"""Merged pull/merge requests through the gh and glab CLIs, and signals derived from them."""
import json
from concurrent.futures import ThreadPoolExecutor
import subprocess
from datetime import date, datetime
from pathlib import Path

from gitfacts import git

MIN_DESCRIPTION = 20
LARGE_DIFF_LINES = 400
FAST_MERGE_MINUTES = 30

GH_FIELDS = "number,title,body,url,author,createdAt,mergedAt,additions,deletions,files,reviews,mergeCommit"


def detect_host(root: Path):
    try:
        url = git(root, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        return None
    if "github.com" in url:
        return "github"
    if "gitlab" in url:
        return "gitlab"
    return None


def _github(item: dict) -> dict:
    human = {r["author"]["login"] for r in item.get("reviews", [])
             if r.get("state") == "APPROVED" and r.get("author") and not r["author"]["login"].endswith("[bot]")}
    return {"number": item["number"], "title": item["title"], "body": item.get("body") or "",
            "url": item["url"], "author": (item.get("author") or {}).get("login", ""),
            "created_at": item["createdAt"], "merged_at": item["mergedAt"], "approvals": len(human),
            "additions": item.get("additions", 0), "deletions": item.get("deletions", 0),
            "files": [f["path"] for f in item.get("files", [])],
            "commits": [c["oid"] for c in item.get("commits", [])],
            "merge_commit": (item.get("mergeCommit") or {}).get("oid")}


def _gitlab(item: dict) -> dict:
    return {"number": item["iid"], "title": item["title"], "body": item.get("description") or "",
            "url": item["web_url"], "author": (item.get("author") or {}).get("username", ""),
            "created_at": item["created_at"], "merged_at": item["merged_at"], "approvals": None,
            "additions": 0, "deletions": 0, "files": [], "commits": [], "merge_commit": None}


def merged_prs(root: Path, since: date, until: date, host, run=subprocess.run, base: str = None):
    """Returns (prs, warnings). Never raises when the CLI is missing or fails."""
    if host == "github":
        cmd = ["gh", "pr", "list", "--state", "merged", "--search", f"merged:>={since.isoformat()}",
               "--limit", "200", "--json", GH_FIELDS] + (["--base", base] if base else [])
        convert = _github
    elif host == "gitlab":
        cmd = ["glab", "mr", "list", "--merged", "--per-page", "100", "-F", "json"] + (["--target-branch", base] if base else [])
        convert = _gitlab
    else:
        return [], []
    try:
        out = run(cmd, cwd=root, capture_output=True, text=True)
    except FileNotFoundError:
        return [], [f"{cmd[0]} not available: PR data skipped"]
    if out.returncode != 0:
        lines = out.stderr.strip().splitlines()
        return [], [f"{cmd[0]} failed: PR data skipped ({lines[0] if lines else 'no error output'})"]
    try:
        items = json.loads(out.stdout or "[]")
    except ValueError:
        return [], [f"{cmd[0]} returned invalid JSON: PR data skipped"]
    prs = [convert(i) for i in items if i.get("mergedAt") or i.get("merged_at")]
    # the date part of merged_at, UTC as given
    prs = [p for p in prs if since.isoformat() <= p["merged_at"][:10] <= until.isoformat()]
    warnings = []
    if host == "github":  # `commits` in the list query exceeds GitHub's node limit, so ask per PR
        def view_commits(pr):
            try:
                view = run(["gh", "pr", "view", str(pr["number"]), "--json", "commits"],
                           cwd=root, capture_output=True, text=True)
                if view.returncode != 0:
                    raise ValueError(view.stderr.strip().splitlines()[0] if view.stderr.strip() else "no error output")
                pr["commits"] = [c["oid"] for c in json.loads(view.stdout)["commits"]]
            except (OSError, ValueError, KeyError, TypeError) as e:
                return f"gh pr view #{pr['number']} failed: its commits are unknown ({e})"

        with ThreadPoolExecutor(max_workers=8) as pool:  # map keeps the PR order
            warnings = [w for w in pool.map(view_commits, prs) if w]
    return prs, warnings


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pr_signals(prs: list[dict]) -> list[dict]:
    found = []
    for pr in prs:
        ref = f"#{pr['number']}"
        if pr["approvals"] == 0:
            found.append({"type": "no_human_approval", "ref": ref, "note": f"{ref} merged without a human approval"})
        if len(pr["body"].strip()) < MIN_DESCRIPTION:
            found.append({"type": "empty_description", "ref": ref, "note": f"{ref} has no real description"})
        lines = pr["additions"] + pr["deletions"]
        minutes = (_time(pr["merged_at"]) - _time(pr["created_at"])).total_seconds() / 60
        if lines >= LARGE_DIFF_LINES and minutes <= FAST_MERGE_MINUTES:
            found.append({"type": "fast_large_merge", "ref": ref,
                          "note": f"{ref} changed {lines} lines and merged after {round(minutes)} min"})
    return found
