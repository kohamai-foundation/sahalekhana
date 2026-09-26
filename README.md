# Sahalekhana — सहलेखन

Co-writing a research paper with an AI, and keeping an honest record of who did
what. A Django app plus the ontology behind it.

The idea: a writer composes in LaTeX; the AI is an interlocutor whose role the
writer chooses; every act on either side is typed by a device from
**tantrayukti** (the classical Indian grammar of composing a treatise),
conducted under the rules of **vāda** (Nyāya's truth-seeking discussion), and
committed to git. Reviewers read the paper with its making attached.

**The AI never writes to the paper.** No tool it is given can. At most it puts
candidates in an ask panel, where an author examines and accepts them one by one.

- `docs/concept-note.html` — the concept note: the grammar, the conduct, the
  ledger design, and a worked example. Open it in a browser.
- `ontology/sahalekhana.ttl` — the OWL ontology (aligned to W3C PROV-O):
  32 tantrayukti devices, 40 act classes, kāraka roles, Nyāya's 5 pseudo-reasons
  and 22 points of failure, deliberation threads, disclosure levels.
- `lekhana/` — the portal: a reusable Django app.

## What it records

Each paper is one git repository. Every commit ends in `Act:` trailers — the
act, the agent (`human:<name>` or `ai:<model>`), the kāraka role, the device,
the unit, whether the edit changes what the paper asserts, and any defect named
in a rejection. Alongside the source it keeps `.sahalekhana/`: every request to
the AI with its candidates (rejected ones included), every discussion turn,
review points, recorded ideas, and the method steps.

Three things fall out of that:

- **Contribution as a profile, not a percentage.** Counts by kind of act, for
  each side. Accepting an AI candidate without opening its check is recorded as
  exactly that.
- **Attribution of ideas.** Who first put an idea, framing or term on the table.
  The AI's claim about that stays *unconfirmed* until a person confirms or
  corrects it.
- **How the text arrived.** Characters typed, and for each paste its length and
  a SHA-256 — never the text. The hash is matched against the portal's own AI
  candidates, so a paste reads as from the ask panel, from elsewhere in the
  paper, or from outside. Per-paper switch, on by default.

## Using it

Add the app to a Django 5.2 project:

```python
INSTALLED_APPS = [..., "lekhana"]
LEKHANA_REPO_ROOT = BASE_DIR / "lekhana_repos"   # one git repo per paper
LEKHANA_MODEL = "claude-opus-5"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")   # AI is off without it
LEKHANA_LATEX_CMD = "latexmk -pdf -interaction=nonstopmode -halt-on-error"
```

```python
urlpatterns += [path("lekhana/", include("lekhana.urls"))]
```

Then `manage.py migrate`. It needs `git` on the host, and `latexmk` only for the
PDF button; without LaTeX the portal says so and the export still carries the
source. Authentication is your project's: every view requires a signed-in user,
and access to a paper is an author or reviewer membership.

- `manage.py anchor_lekhana [--ots]` folds every paper's HEAD into one SHA-256
  and, with `--ots`, timestamps it through OpenTimestamps (only the hash leaves).
- `manage.py test lekhana` runs the suite (167 tests, no network: the AI is mocked).

## Status

Running in production at heritagesemantics.org since September 2026, behind a
login. Honest limits: the live Claude path has only ever been exercised with
mocks, because no API key is set on that deployment yet; an AI request holds a
web worker while it runs; commits are not signed with per-author keys, so
tamper-evidence rests on the anchoring; and the concept note's planned
evaluation (typing real review exchanges, measuring inter-annotator agreement,
writing one real paper in the portal) has not been done.

## Licence

None yet — all rights reserved for the moment. If you want to use or build on
this, please ask; a licence will be added.
