import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from fixtures import TmpDirCase
from test_content import good_content
import render

SCRIPT = Path(render.__file__)
WEEK = "2026-W46"


def facts(**over):
    f = {"period": {"since": "2026-11-09", "until": "2026-11-15", "week": WEEK},
         "repo": {"name": "demo", "root": "r", "default_branch": "main", "host": None},
         "user": {"name": "Ada", "email": "ada@example.com"},
         "sources": {"git": True, "prs": True, "sessions": 1, "specs": []},
         "commits": [{"sha": "abcdef1234567", "files": [{"path": "src/a.py"}]}], "hotzones": {"files": [{"path": "a.py", "commits": 2, "lines": 9}],
                                                  "dirs": []},
         "risks": {"untested": [], "dependencies": [], "migrations": []}, "blind_spots": [],
         "prs": [], "repo_signals": [], "priorities": [], "sessions": [{"id": "s"}],
         "digests": {"features": [], "sessions": []}, "warnings": []}
    f.update(over)
    return f


class RenderCase(TmpDirCase):
    def make(self, content=None, f=None, week=WEEK):
        d = self.tmp / ".debrief" / week
        d.mkdir(parents=True)
        (d / "facts.json").write_text(json.dumps(f or facts()), encoding="utf-8")
        (d / "content.json").write_text(json.dumps(content or good_content()), encoding="utf-8")
        return d

    def data(self, html):
        m = re.search(r'<script id="debrief-data" type="application/json">(.*?)</script>', html, re.S)
        return json.loads(m.group(1))


class TestRender(RenderCase):
    def test_render_inlines_data(self):
        d = self.make()
        html = render.render(d).read_text(encoding="utf-8")
        data = self.data(html)
        self.assertEqual(set(data), {"facts", "content", "history", "week", "role"})
        self.assertEqual(data["facts"], facts())
        self.assertEqual(data["content"], good_content())
        self.assertEqual((data["week"], data["role"], data["history"]), (WEEK, "technical", []))
        self.assertEqual(render.render(d), d / "index.html")

    def test_role_from_config(self):
        d = self.make()
        (d.parent / "config.json").write_text('{"role": "business"}')
        self.assertEqual(self.data(render.render(d).read_text())["role"], "business")

    def test_script_close_tag_escaped(self):
        body = "</script><script>alert(1)</script> <!-- x"
        f = facts(prs=[{"number": 1, "title": "t", "body": body, "url": "https://x/1"}])
        html = render.render(self.make(f=f)).read_text(encoding="utf-8")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertEqual(self.data(html)["facts"]["prs"][0]["body"], body)

    def test_history_reads_past_weeks(self):
        root = self.tmp / ".debrief"
        (root / "2026-W44").mkdir(parents=True)
        (root / "2026-W44" / "results.json").write_text(json.dumps({"score": {"correct": 3, "total": 5}}))
        (root / "2026-W45").mkdir()
        (root / "2026-W45" / "facts.json").write_text(json.dumps(facts()))
        (root / "2026-W43").mkdir()
        (root / "2026-W43" / "results.json").write_text("{broken")
        (root / "notes").mkdir()
        h = render.history(root, WEEK)
        self.assertEqual([x["week"] for x in h], ["2026-W43", "2026-W44", "2026-W45"])
        self.assertEqual(h[0], {"week": "2026-W43", "score": None, "hotzones": []})
        self.assertEqual(h[1]["score"], {"correct": 3, "total": 5})
        self.assertEqual(h[2], {"week": "2026-W45", "score": None, "hotzones": ["a.py"]})

    def test_history_caps_at_12(self):
        root = self.tmp / ".debrief"
        for w in range(1, 21):
            (root / f"2026-W{w:02d}").mkdir(parents=True)
        h = render.history(root, "2026-W30")
        self.assertEqual((len(h), h[0]["week"], h[-1]["week"]), (12, "2026-W09", "2026-W20"))

    def test_review_without_sessions_shows_note(self):
        c = good_content()
        c["review"]["conversations"] = None
        html = render.render(self.make(content=c, f=facts(sessions=[]))).read_text()
        self.assertIn('data-i18n="review.no_sessions"', html)

    def test_empty_week_renders(self):
        c = good_content()
        c.update(features=[], visible_changes=[], open_questions=[])
        c["quiz"] = {"business": [], "technical": []}
        c["review"]["conversations"] = None
        f = facts(commits=[], sessions=[], hotzones={"files": [], "dirs": []})
        html = render.render(self.make(content=c, f=f)).read_text()
        self.assertIn('data-i18n="empty.week"', html)

    def test_week_tab_first_with_features_before_numbers(self):
        html = render.render(self.make()).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'data-tab="(\w+)"', html), ["week", "quiz", "review"])
        self.assertIn('let tab = "week";', html)
        self.assertLess(html.index('id="week-features"'), html.index('id="week-numbers"'))

    def test_invalid_content_exits_1(self):
        c = good_content()
        c["lang"] = "xx"
        d = self.make(content=c)
        r = subprocess.run([sys.executable, str(SCRIPT), str(d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("lang", r.stdout + r.stderr)
        self.assertFalse((d / "index.html").exists())

    def test_cli_renders(self):
        d = self.make()
        r = subprocess.run([sys.executable, str(SCRIPT), str(d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((d / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
