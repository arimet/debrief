"""Find the user's Claude Code sessions for a repository and turn them into redacted digests."""
from __future__ import annotations

import json
import re
from pathlib import Path

import cr_extract
from gitfacts import git

SESSION_DIGEST_MAX_CHARS = 60_000


APPROVAL = re.compile(
    r"^(ok(ay)?|oui|ouais|yes|yep|yeah|go|vas[- ]y|continue|continues|lgtm|parfait|perfect|merci|thanks|"
    r"d'accord|ok go|go on|do it|fais[- ]le|top|nickel|super|great|good|c'est bon|ça marche|ca marche|"
    r"valide|validé|approved|sounds good|1|2|3)[\s!.,]*$", re.I)


def pen(events: list) -> dict:
    """Who holds the pen: the user's messages, and how many of them only approve ("ok", "vas-y")."""
    texts = [e.get("text", "").strip() for e in events if e.get("kind") == "user_message"]
    return {"messages": len(texts), "approvals": sum(bool(APPROVAL.match(t)) for t in texts)}


def worktree_roots(root: Path) -> list[Path]:
    out = git(root, "worktree", "list", "--porcelain")
    return [Path(line[9:]) for line in out.splitlines() if line.startswith("worktree ")]


def _bounds(path: Path):
    """First cwd and last timestamp of a session, or None when unreadable."""
    cwd = stamp = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                cwd = cwd or row.get("cwd")
                stamp = row.get("timestamp") or stamp
    except (OSError, UnicodeDecodeError):
        return None
    return (cwd, stamp) if cwd and stamp else None


def _inside(cwd: str, roots: list[Path]) -> bool:
    cwd = Path(cwd).resolve()
    return any(cwd == r or r in cwd.parents for r in (x.resolve() for x in roots))


def find_sessions(roots: list[Path], since: str, until: str, projects_dir: Path | None = None) -> list[Path]:
    projects_dir = cr_extract.PROJECTS_DIR if projects_dir is None else Path(projects_dir)
    if not projects_dir.is_dir():
        return []
    found = []
    for path in sorted(projects_dir.glob("*/*.jsonl")):
        bounds = _bounds(path)
        if bounds and since <= bounds[1][:10] <= until and _inside(bounds[0], roots):
            found.append(path)
    return found


def session_digest(path: Path, max_chars: int = SESSION_DIGEST_MAX_CHARS, home: str | None = None) -> tuple[dict, str]:
    facts = cr_extract.build_facts(path, home=str(Path.home()) if home is None else home)
    s = facts["session"]
    text = cr_extract.digest(facts)
    truncated = len(text) > max_chars
    if truncated:
        text = _start_and_end(text, max_chars)
    return {"id": s["id"], "title": s["title"], "date": s["ended_at"][:10], "truncated": truncated,
            "pen": pen(facts["events"])}, text


def _start_and_end(text: str, max_chars: int) -> str:
    """Keep the first third and the last two thirds of the budget, cut on line boundaries."""
    lines = text.split("\n")
    room = max_chars - len("[... 999999 lines omitted ...]") - 2
    head, tail, used = [], [], 0
    for line in lines:
        if used + len(line) + 1 > room // 3:
            break
        head.append(line)
        used += len(line) + 1
    used = 0
    for line in reversed(lines[len(head):]):
        if used + len(line) + 1 > room - room // 3:
            break
        tail.append(line)
        used += len(line) + 1
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"[... {omitted} lines omitted ...]"] + tail[::-1])
