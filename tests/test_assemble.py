import json
import subprocess
import sys

from fixtures import TmpDirCase
import assemble


def pr(n, size, title="T"):
    return {"number": n, "title": title, "url": f"https://x/pull/{n}",
            "merged_at": "2026-11-12T09:30:00Z", "additions": size, "deletions": 0}


def feature(fid):
    return {"id": fid, "title": fid, "summary": "S", "cases_handled": ["a"], "cases_not_handled": [],
            "provenance": "pr", "ref": {"pr": 1}, "merged_on": "2026-11-12"}


class AssembleTest(TmpDirCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "summaries").mkdir()
        self.facts = {"prs": [pr(1, 10), pr(2, 500), pr(3, 9999, "Bump lodash")], "commits": [],
                      "digests": {"features": ["digests/feature-1.md", "digests/feature-2.md",
                                               "digests/feature-3.md"],
                                  "unlinked": ["digests/commits-unlinked-1.md"],
                                  "trivial": ["digests/feature-3.md"], "sessions": []}}
        (self.tmp / "facts.json").write_text(json.dumps(self.facts))

    def card(self, stem, *fids):
        data = {"features": [feature(f) for f in fids],
                "visible_changes": [{"feature_id": fids[0], "text": f"vc {fids[0]}", "provenance": "pr"}],
                "open_questions": [{"feature_id": fids[0], "text": f"oq {fids[0]}"}]}
        (self.tmp / "summaries" / f"{stem}.card.json").write_text(json.dumps(data))

    def run_cli(self):
        return subprocess.run([sys.executable, assemble.__file__, str(self.tmp), "--lang", "fr"],
                              capture_output=True, text=True)

    def test_assembles_cards_in_order_with_placeholders(self):
        self.card("feature-1", "pr-1")
        self.card("feature-2", "pr-2")
        self.card("commits-unlinked-1", "commits-2", "commits-1")
        out = self.run_cli()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "")
        content = json.loads((self.tmp / "content.json").read_text())
        self.assertEqual(content["lang"], "fr")
        # non-trivial PRs by size, then commit themes (card order kept), trivial PRs last
        self.assertEqual([f["id"] for f in content["features"]],
                         ["pr-2", "pr-1", "commits-2", "commits-1", "pr-3"])
        self.assertEqual([v["feature_id"] for v in content["visible_changes"]], ["pr-2", "pr-1", "commits-2"])
        self.assertEqual(len(content["open_questions"]), 3)
        self.assertEqual(content["quiz"], {"business": [], "technical": []})
        self.assertEqual(content["review"], {"repository": None, "conversations": None})

    def test_trivial_card(self):
        content, _ = assemble.assemble(self.tmp, "en")
        self.assertEqual(content["features"], [{
            "id": "pr-3", "title": "Bump lodash",
            "summary": "Technical update, no functional change stated in the PR.",
            "cases_handled": [], "cases_not_handled": [], "provenance": "pr",
            "ref": {"pr": 3, "url": "https://x/pull/3"}, "merged_on": "2026-11-12"}])

    def test_missing_card_reported(self):
        self.card("feature-1", "pr-1")
        out = self.run_cli()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stderr.splitlines(),
                         ["no card for digests/feature-2.md", "no card for digests/commits-unlinked-1.md"])

    def test_duplicate_id_fails(self):
        self.card("feature-1", "pr-1")
        self.card("feature-2", "pr-1")
        out = self.run_cli()
        self.assertEqual(out.returncode, 1)
        self.assertIn("pr-1", out.stderr)
        self.assertFalse((self.tmp / "content.json").exists())
