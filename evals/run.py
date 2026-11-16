"""Run debrief end to end on the fixture week with Claude Code, then check the result.

    python3 evals/run.py [--model MODEL] [--keep DIR]

1. Builds the fixture repository and extracts its week (no AI, see fixture.py).
2. Runs `claude -p` in that repository: steps 4 to 6 of SKILL.md (summaries, cards, quiz, review),
   then step 9 (the retelling comparison). This costs a few minutes of Claude.
3. Runs check.py on the week folder.

Run it after changing SKILL.md, the prompts it holds, or when a new model comes out.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent / "skills" / "debrief"
sys.path.insert(0, str(HERE))
import check  # noqa: E402
import fixture  # noqa: E402

PROMPT = """You are running the debrief skill on a test repository, unattended. Read {skill}/SKILL.md first.

The extraction (step 3) is done: the week folder is {week}, and <skill-dir> is {skill}.
Role and language are set: content.lang is en. Do not ask the user anything.

Run step 4 (Digest), step 5 (Write content.json) and step 6 (Validate) exactly as SKILL.md says.
Then run step 9 (Compare the retelling): the retelling is already in {week}/results.json.
Do not run render.py or serve.py. Stop once content.py prints ok."""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", help="model passed to claude -p (default: Claude Code's default)")
    ap.add_argument("--keep", help="build in this empty folder and keep it, instead of a temporary one")
    args = ap.parse_args(argv)
    if not shutil.which("claude"):
        print("claude (Claude Code CLI) is not on PATH", file=sys.stderr)
        return 2

    root = Path(args.keep).resolve() if args.keep else Path(tempfile.mkdtemp(prefix="debrief-eval-"))
    week = fixture.extract_week(root)
    print(f"fixture week: {week}")
    cmd = ["claude", "-p", PROMPT.format(skill=SKILL_DIR, week=week), "--add-dir", str(SKILL_DIR),
           "--allowedTools", "Bash,Read,Write,Edit,Glob,Grep,Agent,Task"]
    if args.model:
        cmd += ["--model", args.model]
    started = time.time()
    run = subprocess.run(cmd, cwd=week.parents[1], capture_output=True, text=True)
    print(f"claude -p: exit {run.returncode} after {time.time() - started:.0f} s")
    if run.returncode != 0:  # claude -p prints some errors (expired login) on stdout
        print((run.stderr or run.stdout)[-2000:], file=sys.stderr)
    if not (week / "content.json").exists():
        print("no content.json written", file=sys.stderr)
        return 1
    code = check.main(["check.py", str(week)])
    if not args.keep:
        print(f"(files kept in {root} for inspection)")
    return code


if __name__ == "__main__":
    sys.exit(main())
