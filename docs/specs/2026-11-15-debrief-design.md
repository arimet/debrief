# debrief — design

> Working name, to confirm. Status: draft, 2026-11-15.

## Intent

AI now writes a growing share of a team's code. The risk is that the team loses its grip on what was built: features nobody can explain to the client, code nobody has read, decisions taken in a Claude conversation and never questioned.

`debrief` is a Claude Code skill that each team member runs once a week, by hand, inside the project repository. It looks at what was delivered over the period and produces one local HTML page that answers one question: **do I still have this week's work in my head?**

Two roles use it:

- **Business** (the person who carries the business side): quizzed on features and business cases, sees a feature recap.
- **Technical**: quizzed on the code that changed, sees hotzones and risk areas.

Both get a review of how the week's work was done with AI: from the repository (PRs, commits) on any machine, and from their own Claude conversations when those exist on the machine.

## Goals

- One command, one page, three tabs: **Quiz**, **Dashboard**, **Review**, with a Business / Technical switch.
- Cover the whole team's work on the period, not only the user's own commits. The point is to know the project, not to check one's own work.
- Completing the quiz takes 10 to 15 minutes.
- Wrong answers point to where to read (`file:line` for the technical role, the PR for the business role).
- Results stay private by default; the user can export a summary to send by hand (lead, retro).

## Non-goals

- Team-wide dashboard or score aggregation. Each person runs it on their own machine.
- Scheduling. The user runs it by hand (e.g. on Friday).
- Multi-repository projects. One run = the current repository.
- Ticket tools (Jira, Linear) in v1. Business knowledge comes from PRs/MRs, specs in the repo, and inference.
- Replacing `handoff` (per-feature quiz) or `conversation-review` (per-session report). `debrief` is the weekly layer above them.

## Anti-goals (failure even if it works)

- **Surveillance.** If it feels like a manager's tool, people stop answering honestly. Nothing leaves the machine unless the user exports it.
- **Trivia quiz.** Questions about variable names or line counts. Questions must be about behavior, business rules, where logic lives, and why.
- **Made-up business rules presented as fact.** When a rule is inferred from code rather than read in a PR or spec, it must say so.
- **20 reports nobody reads.** The review is one weekly synthesis, not a list of per-session reports.

## Constraints

- Claude Code sessions are stored locally (`~/.claude/projects/`). The conversation part of the Review tab only sees the current user's sessions for this project.
- **Sessions are optional.** A machine with only the git repository (no Claude sessions, or no `gh`/`glab`) still gets the full page: quiz, dashboards, and the repository part of the Review tab. Only the conversation part disappears.
- The page is static (opened from disk): it cannot call Claude. Open questions are self-assessed.
- Python standard library only (same as `conversation-review`), so the skill installs with no dependencies.
- Code, docs and identifiers in English. Page UI in French and English, following the user's language.
- Pilot volume: a medium week, 4 to 8 people, 10 to 30 merged PRs, 10+ Claude sessions per person. One single pass over everything will not fit: the pipeline must summarize before it writes (see step 2).
- Internal first (one client project as pilot), open source later. Nothing in the repo that ties it to a company or to a person (paths, names, emails). No AI tool files versioned, no Claude trailer in commits.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | **Hybrid engine.** `extract.py` collects facts and computes metrics → Claude writes `content.json` → `render.py` fills an HTML template. | Numbers are exact (computed by the script), judgment comes from Claude, layout is stable week to week so weeks compare. Reuses `conversation-review`'s session extractor and redaction. Costs: code to maintain. |
| B | Skill instructions only. Claude runs git commands and writes the HTML each time. | Nothing to maintain, but layout drifts between weeks, metrics can be wrong, and each run is token-heavy. |
| C | Add a weekly mode to `handoff` and `conversation-review`, plus a new business-role skill and a hub page. | Maximum reuse, but three tools to install and three renderings, which contradicts the single interface. |

Chosen: **A**, the only option that keeps a single interface and reliable numbers.

## How it works

```
/debrief [--since 2026-11-08] [--until 2026-11-15] [--role business|technical]
   │
   ├─ 1. extract.py  → .debrief/2026-W46/facts.json      (deterministic)
   ├─ 2. Claude      → .debrief/2026-W46/content.json    (judgment)
   └─ 3. render.py   → .debrief/2026-W46/index.html      (opened in browser)
```

### 1. Facts (`extract.py`)

Period defaults to the current ISO week, Monday to today (the previous full week on a Monday) (my call). Sources:

- **git**: commits on the period (all authors), diff stats per file, `git blame` for blind spots.
- **PRs/MRs** merged on the period, via `gh` or `glab` when available: title, description, review comments, linked issues.
- **Specs in the repo**: docs, ADRs, spec files touched or referenced on the period.
- **Claude sessions** of this project for the current user, on the period (reuse `conversation-review/extract.py` and `redact.py`).

Computed metrics (technical dashboard):

- **Hotzones**: files and directories ranked by commits and lines changed.
- **Untested changes**: source files changed with no test file changed alongside; new dependencies; schema migrations.
- **Blind spots**: areas changed this week that the current user has never authored (via `git blame` / history).

When `gh`/`glab` is missing or there are no PRs, the extractor degrades to git only and records it, so the page can say where its knowledge comes from.

### 2. Content (Claude, guided by `SKILL.md`)

Sized for a medium week, Claude works in two stages:

1. **Digest**: one summary per feature (PR or group of commits, with its diff) and one per Claude session, produced in parallel by subagents. `extract.py` sets caps (diff size per feature, transcript size per session) and records what it truncated. The same subagents also write each feature's card (feature, visible changes, open questions) in the page language; trivial PRs (dependency, CI, test-only, release) get no subagent.
2. **Write**: `assemble.py` merges the cards, plus a fixed card per trivial PR, into `content.json`. Claude then reads `facts.json` plus the summaries, not the raw diffs and transcripts, and fills the quiz and the review. The resulting `content.json` holds:

- **Features**: grouped from PRs/commits, each with a two-line business summary, cases handled / not handled, source PR. Each business statement carries a provenance: `pr`, `spec`, `commit` (rule stated in a commit message the author wrote), or `inferred` (only when no written source says it).
- **Visible changes**: what changes for the end user (screens, rules, messages, removed behavior).
- **Open questions**: rules inferred without a source, TODOs, unhandled edge cases, to check with the client.
- **Quiz**, one per role, 5 to 10 questions scaled to the volume of the week:
  - Business: business behavior and cases, in non-technical words ("A user does X when Y. What happens?"). Feedback links to the PR.
  - Technical: where logic lives, behavior, why a choice was made. Feedback links to `file:line`.
  - Mostly multiple choice (auto-graded). "Why" questions are open: the user types an answer, the page reveals the expected answer, the user rates themselves (got it / partly / missed).
  - Questions based on inferred rules are flagged as such.
  - **Priority when the week is larger than the quiz**: blind spots first (work the user did not author: other people's and the AI's), then risk areas (hotzones, untested changes, migrations). The user's own work only fills remaining slots. `extract.py` computes a priority per feature/file from authorship and risk so the choice is not left to Claude.
- **Review**, in two parts, same Intent / Evidence / Control grid from `conversation-review`:
  - **Repository review (always shown)**, from git and PRs, so it works on any machine. Signals computed by `extract.py`: PRs merged without a human approval, commits co-authored by an AI (trailer) with no test change, PRs with an empty or one-line description, large diffs merged shortly after opening. Claude turns them into a short synthesis and the 3 to 5 most notable PRs/commits, each with why it matters. Team-level, not about the current user.
  - **Conversation review (when sessions exist)**: one weekly synthesis of the current user's sessions: trends of the week (prompt framing, edits accepted without verification, scope drift), then the 3 to 5 most problematic moments, each with a redacted excerpt, why it matters, and the prompt that would have avoided it. Same grid for both roles.
- **Redaction**: only the excerpts that reach the Review tab are redacted (reusing `redact.py`), automatically, since the page stays local. The per-item confirmation that `conversation-review` requires happens only when the user exports.

### 3. Page (`render.py` + template)

A single self-contained HTML file: template with `facts.json` and `content.json` inlined. Opened automatically at the end of the run.

- Header: period, project, role switch (Business / Technical), data sources used.
- **Quiz** tab: one question at a time, feedback after each answer, final score and the areas to re-read.
- **Dashboard** tab:
  - Business: feature list, timeline of the week (day by day, what was merged), visible changes, open questions.
  - Technical: hotzones, untested changes, blind spots.
- **Review** tab: repository review always; conversation review below it when sessions were found, otherwise a one-line note saying none were found on this machine.
- **Export** button: writes a summary (score, self-assessments, review highlights) to share by hand. Format left to the builder (HTML or Markdown).

Each week stays in `.debrief/<ISO week>/`. `.debrief/` is excluded from git (added to `.git/info/exclude`, not `.gitignore`, so the project repo is not touched).

**Trends across weeks (v1)**: the page shows the user's quiz score over past weeks and how hotzones move.

- Hotzones history: `render.py` inlines the `facts.json` of previous week folders.
- Score history: answers must reach the disk, which a page opened from disk cannot do. The skill serves the page through a small standard-library local server (`serve.py`) that writes answers to `.debrief/<week>/results.json` as the user goes; `render.py` inlines past `results.json` files. When the page is reopened from disk without the server, answers fall back to localStorage, and a button saves them as a `results.json` the next run picks up.

## Producing before reading (added 2026-11-16)

Taken from Thomas Pierrain's "Back in the equation" (Slicer, a design harness where the tool asks and the human produces): passivity, reading and approving all day, is what loses the grip on the domain. Debrief applies it after the fact.

- **Retelling.** The Quiz tab opens on a retelling of the week written from memory, saved in `results.json` (`retelling`). On request (step 9 of `SKILL.md`), Claude compares it claim by claim with the PRs: `same`, `drift`, `new`, `word` (vocabulary that differs from the sources), plus the features never mentioned. The comparison is `content.retelling` and opens the Bilan tab.
- **Decision questions.** Quiz type `decision`: the user picks an option and writes what they rule out before the team's choice is revealed. A different choice is "not the team's choice", not a wrong answer; it still counts as a miss in the score.
- **Weakened tests.** `extract.py` adds a `tests_weakened` repository signal when a PR removes assertions or test cases from test files while changing other files (net count, so tests moved between files do not count).
- **Who holds the pen.** Each session gets `pen: {messages, approvals}` (messages that only approve); the conversation review gains a fourth axis, `pen`.
- **Not taken:** blurring the features until the quiz is done (conflicts with landing on the shared feature list), slicing and Definition of Done tests (upstream of the code, not a weekly debrief).

## Decided in conversation

- Each person runs it on their own machine, by hand, once a week.
- Business sources: PRs/MRs, specs in the repo, and inference from code and context when nothing else exists.
- Everything in one HTML page; quiz answered in the page.
- Multiple choice + self-assessment for open questions.
- Results private; voluntary export.
- Technical scope: the whole team's commits.
- Review: weekly synthesis + worst moments; the business role gets it too, same grid.
- Technical dashboard: hotzones, untested changes, blind spots (not the AI share metric).
- Business dashboard: feature list, timeline, visible changes, open questions.
- Manual trigger. Internal first, open source later, code in English.
- Approach A.
- Pilot is a medium week (10 to 30 PRs).
- Quiz priority: blind spots first, then risk areas, own work last.
- Trends across weeks in v1.
- Sessions optional: Review always has a repository part (git + PRs); the conversation part is added when sessions exist on the machine.

## My calls (correct if wrong)

- Working name `debrief`, command `/debrief`.
- Default period: current ISO week (Monday to today; previous full week when today is Monday); overridable with `--since` / `--until` (one bound alone: `until` defaults to today, `since` to the Monday of `until`'s week).
- Role asked on first run and remembered (`.debrief/config.json`); the page switch shows both views anyway.
- Output in `.debrief/<ISO week>/`, excluded via `.git/info/exclude`.
- Commits counted on the default branch plus PRs merged on the period.
- Inferred business rules shown with an "inferred" badge, in the dashboard and in the quiz.
- Redaction automatic on Review excerpts only, confirmation at export only.
- Digest stage with subagents and size caps, to fit a medium week.
- Local stdlib server to save quiz results to disk, localStorage as fallback.

## Left to the builder

- Visual design of the page (to be done with mockups before building; charts for hotzones and timeline).
- Exact JSON schemas for `facts.json` and `content.json`.
- How "test file" is detected per stack (naming conventions, configurable list).
- Matching Claude sessions to the project, including git worktrees.
- Export format.
- Whether to vendor or import the shared `conversation-review` scripts.
