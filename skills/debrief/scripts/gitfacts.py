"""Commit facts from git: commits in a period and the files that change most."""
from __future__ import annotations
import re
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path, PurePosixPath

AI_TRAILER = re.compile(r"(?im)^co-authored-by:.*(claude|anthropic|copilot|cursor|codex|gemini|openai)")
BRACE_RENAME = re.compile(r"\{[^{}]* => ([^{}]*)\}")


def git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, check=True, capture_output=True, text=True)
    return out.stdout


def default_branch(root: Path) -> str:
    try:
        return git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip().removeprefix("origin/")
    except subprocess.CalledProcessError:
        return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _new_path(path: str) -> str:
    if " => " not in path:
        return path
    if "{" in path:
        return BRACE_RENAME.sub(r"\1", path).replace("//", "/").lstrip("/")
    return path.split(" => ")[1]


def _count(value: str):
    return None if value == "-" else int(value)


def commits(root: Path, since: date, until: date, ref: str) -> list[dict]:
    fmt = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1f"
    raw = git(root, "log", ref, "--no-merges", f"--since={since.isoformat()} 00:00:00",
              f"--until={until.isoformat()} 23:59:59", "--numstat", f"--format={fmt}")
    found = []
    for record in raw.split("\x1e")[1:]:
        sha, author, email, when, subject, body, stats = record.split("\x1f")
        files = []
        for line in stats.strip().splitlines():
            added, removed, path = line.split("\t", 2)
            files.append({"path": _new_path(path), "added": _count(added), "removed": _count(removed)})
        found.append({"sha": sha, "author": author, "email": email, "date": when, "subject": subject,
                      "body": body.strip(), "ai_coauthored": bool(AI_TRAILER.search(body)), "files": files})
    return found


def hotzones(commits: list[dict], top: int = 10) -> dict:
    def rank(stats):
        rows = [{"path": p, "commits": len(s["shas"]), "lines": s["lines"]} for p, s in stats.items()]
        return sorted(rows, key=lambda r: (-r["commits"], -r["lines"], r["path"]))[:top]

    files = defaultdict(lambda: {"shas": set(), "lines": 0})
    dirs = defaultdict(lambda: {"shas": set(), "lines": 0})
    for c in commits:
        for f in c["files"]:
            lines = (f["added"] or 0) + (f["removed"] or 0)
            for stats, key in ((files, f["path"]), (dirs, str(PurePosixPath(f["path"]).parent))):
                stats[key]["shas"].add(c["sha"])
                stats[key]["lines"] += lines
    return {"files": rank(files), "dirs": rank(dirs)}


SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".rb", ".go", ".rs", ".java", ".kt",
              ".php", ".cs", ".ex", ".exs", ".scala", ".swift"}
DEP_NAMES = {"package.json", "pyproject.toml", "Gemfile", "go.mod", "Cargo.toml", "composer.json"}
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
TEST_NAME = re.compile(r"(^test_.*|.*_test\.[^.]+|.*_spec\.[^.]+|.*\.(test|spec)\.[^.]+)$")


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(TEST_DIRS & set(p.parts[:-1])) or bool(TEST_NAME.match(p.name))


ASSERTION = re.compile(r"^\s*assert\s|\b(assert\w*|expect|Check\.That|verify|should)\s*[(.]|\.(should|to|toBe|toEqual)\b")
TEST_CASE = re.compile(r"^\s*(it|test|describe|context)(\.\w+)?\s*\(|^\s*(async\s+)?def test_|@Test\b|\[(Fact|Test|Theory)\]")


def weakened_tests(diff: str) -> dict | None:
    """Net assertions and test cases removed from test files by a diff that also changes other files.

    A diff that only moves assertions between test files nets to zero. Returns None when nothing was lost.
    """
    lost = {"assertions": 0, "cases": 0}
    other = False
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            name = line[4:].strip()
            if name != "/dev/null":
                path = name[2:] if name[:2] in ("a/", "b/") else name
                other = other or not is_test(path)
            continue
        if not path or not is_test(path) or line[:1] not in "+-":
            continue
        sign = 1 if line[0] == "-" else -1
        body = line[1:]
        lost["assertions"] += sign * bool(ASSERTION.search(body))
        lost["cases"] += sign * bool(TEST_CASE.search(body))
    return lost if other and (lost["assertions"] > 0 or lost["cases"] > 0) else None


def tracked(root: Path, ref: str = "HEAD") -> set[str]:
    """Files that exist at `ref`, not in the working tree."""
    return set(git(root, "ls-tree", "-r", "--name-only", ref).splitlines())


def risks(commits: list[dict], root: Path, ref: str = "HEAD") -> dict:
    """Changed files that look untested, dependency files and migrations. Files gone at `ref` are skipped."""
    present = tracked(root, ref)
    out = {"untested": [], "dependencies": [], "migrations": []}

    def add(key, path):
        if path in present and path not in out[key]:
            out[key].append(path)

    for c in commits:
        paths = [f["path"] for f in c["files"]]
        tested = any(is_test(p) for p in paths)
        for p in paths:
            name = PurePosixPath(p).name
            if name in DEP_NAMES or (name.startswith("requirements") and name.endswith(".txt")):
                add("dependencies", p)
            if "migration" in p.lower():
                add("migrations", p)
            if not tested and not is_test(p) and PurePosixPath(p).suffix in SOURCE_EXT:
                add("untested", p)
    return out


def blind_spots(root: Path, commits: list[dict], user_email: str, user_name: str = "",
                ref: str = "HEAD") -> list[dict]:
    """Files changed this week that the user never authored a commit on, under any of their identities.

    Identities: the configured email and name, plus every email used under that name in the history.
    A rename keeps the history of the file."""
    present = tracked(root, ref)
    authors: dict[str, list[str]] = {}
    for c in commits:
        for f in c["files"]:
            names = authors.setdefault(f["path"], [])
            if c["author"] not in names:
                names.append(c["author"])

    # one history pass, newest first; `alias` maps a path's older names onto its current name
    log = git(root, "log", ref, "--use-mailmap", "--name-status", "-M", "--format=%x1e%aN%x1f%aE")
    alias: dict[str, str] = {}
    who: dict[str, set] = defaultdict(set)
    everyone = set()
    for entry in log.split("\x1e")[1:]:
        head, _, body = entry.partition("\n")
        ident = tuple(x.lower() for x in head.split("\x1f"))
        everyone.add(ident)
        for line in body.splitlines():
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            if cols[0][0] in "RC" and len(cols) == 3:
                cur = alias.get(cols[2], cols[2])
                who[cur].add(ident)
                if cols[0][0] == "R":
                    alias[cols[1]] = cur
            else:
                who[alias.get(cols[1], cols[1])].add(ident)

    emails = {user_email.lower()}
    name = user_name.lower()
    if name:
        emails |= {e for n, e in everyone if n == name}
    found = []
    for path, names in authors.items():
        if path not in present:
            continue
        if not any(e in emails or (name and n == name) for n, e in who[path]):
            found.append({"path": path, "authors": names})
    return found


def commit_signals(commits: list[dict]) -> list[dict]:
    return [{"type": "ai_without_tests", "ref": c["sha"][:7], "note": c["subject"]}
            for c in commits
            if c["ai_coauthored"] and not any(is_test(f["path"]) for f in c["files"])]
