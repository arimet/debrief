"""The fixture week of evals/fixture.py, extracted, must give exactly tests/golden/facts.json.

Any change to what the scripts measure shows up here as a diff. When the change is intended:
    UPDATE_GOLDEN=1 python3 -m unittest tests.test_golden
"""
import json
import os
import sys
import unittest
from pathlib import Path

from fixtures import TmpDirCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import fixture  # noqa: E402

GOLDEN = Path(__file__).with_name("golden") / "facts.json"


def normalized(facts: dict, tmp: Path) -> dict:
    """The facts with the temporary folder replaced, so they compare across machines."""
    text = json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True)
    return json.loads(text.replace(str(tmp.resolve()), "<tmp>").replace(str(tmp), "<tmp>"))


class GoldenWeekTest(TmpDirCase):
    def test_fixture_week_facts(self):
        week = fixture.extract_week(self.tmp / "fx")
        got = normalized(json.loads((week / "facts.json").read_text(encoding="utf-8")), self.tmp)
        if os.environ.get("UPDATE_GOLDEN"):
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.maxDiff = None
        self.assertEqual(got, json.loads(GOLDEN.read_text(encoding="utf-8")))

    def test_traps_are_in_place(self):
        """The eval relies on these; if one disappears, the eval checks a trap that is not there."""
        facts = json.loads((fixture.extract_week(self.tmp / "fx") / "facts.json").read_text(encoding="utf-8"))
        signals = {(s["type"], s["ref"]) for s in facts["repo_signals"]}
        self.assertIn(("tests_weakened", "#3"), signals)
        self.assertIn(("no_human_approval", "#3"), signals)
        self.assertEqual(facts["digests"]["trivial"], ["digests/feature-4.md"])
        self.assertEqual(len(facts["digests"]["unlinked"]), 1)
        self.assertEqual(facts["sessions"][0]["pen"], {"messages": 4, "approvals": 2})
        blind = {b["path"] for b in facts["blind_spots"]}
        self.assertIn("invoices/fees.py", blind)
        self.assertNotIn("invoices/vat.py", blind)


if __name__ == "__main__":
    unittest.main()
