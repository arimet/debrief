"""Temp git repo builder shared by the tests."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "debrief" / "scripts"))


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class RepoBuilder:
    """A throwaway git repo with dated commits, independent of the machine's git config."""

    def __init__(self, tmp: Path):
        self.root = Path(tmp) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Ada")
        self.git("config", "user.email", "ada@example.com")

    def git(self, *args, env=None):
        full_env = {**os.environ, **(env or {})}
        out = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=self.root,
                             env=full_env, check=True, capture_output=True, text=True)
        return out.stdout.strip()

    def commit(self, files, message, author=("Ada", "ada@example.com"), date="2026-11-10T10:00:00"):
        """files maps path to str/bytes content; None deletes the file. Returns the sha."""
        for path, content in files.items():
            target = self.root / path
            if content is None:
                self.git("rm", "-q", "--", path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode())
            self.git("add", "--", path)
        env = {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
               "GIT_COMMITTER_NAME": author[0], "GIT_COMMITTER_EMAIL": author[1],
               "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        self.git("commit", "-q", "--allow-empty", "-m", message, env=env)
        return self.git("rev-parse", "HEAD")


class FakeRunner:
    """Stands in for subprocess.run. Keys are the command's first three words, else its first two.
    A str is a successful stdout, an Exception is raised, a CompletedProcess is returned as is."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        key3 = " ".join(cmd[:3])
        response = self.responses[key3 if key3 in self.responses else " ".join(cmd[:2])]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return subprocess.CompletedProcess(cmd, 0, stdout=response, stderr="")
        return response


class SessionBuilder:
    """Writes a minimal Claude Code transcript: a user message, an assistant message, one tool call."""

    def __init__(self, cwd, started="2026-11-10T09:00:00.000Z", ended="2026-11-10T09:05:00.000Z",
                 session_id="sess-1", prompt="Add a login page", reply="Done."):
        self.cwd, self.session_id = cwd, session_id
        stamps = [started, started, started, ended]
        base = {"isSidechain": False, "sessionId": session_id, "cwd": str(cwd)}
        self.rows = [
            {**base, "type": "user", "uuid": "u1", "parentUuid": None, "timestamp": stamps[0],
             "message": {"role": "user", "content": prompt}},
            {**base, "type": "assistant", "uuid": "u2", "parentUuid": "u1", "timestamp": stamps[1],
             "message": {"role": "assistant", "id": "m1", "model": "claude-opus-5-5",
                         "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
            {**base, "type": "user", "uuid": "u3", "parentUuid": "u2", "timestamp": stamps[2],
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a.txt"}]},
             "toolUseResult": {}},
            {**base, "type": "assistant", "uuid": "u4", "parentUuid": "u3", "timestamp": stamps[3],
             "message": {"role": "assistant", "id": "m2", "model": "claude-opus-5-5",
                         "content": [{"type": "text", "text": reply}]}},
        ]

    def write(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.session_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in self.rows), encoding="utf-8")
        return path
