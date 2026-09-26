"""How the text arrived: typed here, pasted from the AI panel, or pasted from outside.

The editor reports characters typed and, for each paste, its length and a
SHA-256 of its normalised text. The text itself never leaves the browser. The
server matches the hash against what the portal's own AI proposed and against
the paper's current units, so a paste can be told apart as

* ``ai_candidate`` — text the portal's AI offered in the ask panel,
* ``own_paper``    — text already somewhere in this paper,
* ``outside``      — anything else, including another window.

"Outside" is not an accusation: notes, a co-author's email, an earlier draft
all land there. It is one more column in the record, and the paper's authors
can switch the whole thing off (``Project.track_composition``), which is itself
an act.
"""
from __future__ import annotations

import hashlib
import re

from .models import Candidate, CompositionEvent, Snapshot

SNAPSHOTS_KEPT = 10
_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def digest(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def _known_hashes(project) -> dict[str, int]:
    """Hash → candidate id, for everything the portal's AI has proposed here."""
    rows = Candidate.objects.filter(ask__project=project).values_list("pk", "text")
    return {digest(text): pk for pk, text in rows if text.strip()}


def classify(project, text_hash: str, source_text: str) -> tuple[str, int | None]:
    if not text_hash:
        return CompositionEvent.Source.UNKNOWN, None
    candidate = _known_hashes(project).get(text_hash)
    if candidate:
        return CompositionEvent.Source.AI_CANDIDATE, candidate
    from . import latex
    try:
        units = latex.parse_units(source_text)
    except latex.LatexError:
        units = []
    if any(digest(u.body) == text_hash for u in units) or digest(source_text) == text_hash:
        return CompositionEvent.Source.OWN_PAPER, None
    return CompositionEvent.Source.OUTSIDE, None


def record(*, project, user, events: list[dict], source_text: str) -> list[CompositionEvent]:
    """Store a batch from the editor. Unknown shapes are dropped, not guessed."""
    if not project.track_composition:
        return []
    from . import latex
    made = []
    for event in events[:100]:
        kind = event.get("kind")
        if kind not in (CompositionEvent.Kind.TYPING, CompositionEvent.Kind.PASTE):
            continue
        chars = int(event.get("chars") or 0)
        if chars <= 0:
            continue
        unit_id = str(event.get("unit_id") or "")[:64]
        if unit_id and not latex.UNIT_ID_RE.match(unit_id):
            unit_id = ""
        text_hash = str(event.get("hash") or "")[:64]
        source, candidate_id = ("", None)
        if kind == CompositionEvent.Kind.PASTE:
            source, candidate_id = classify(project, text_hash, source_text)
        made.append(CompositionEvent(project=project, user=user, kind=kind, unit_id=unit_id,
                                     chars=min(chars, 1_000_000), text_hash=text_hash,
                                     source=source, matched_candidate_id=candidate_id))
    return CompositionEvent.objects.bulk_create(made)


def snapshot(*, project, user, content: str) -> Snapshot | None:
    """Save the editor buffer for recovery, and keep only the last few."""
    if not content.strip():
        return None
    latest = project.snapshots.filter(user=user).first()
    if latest and latest.content == content:
        return latest
    saved = Snapshot.objects.create(project=project, user=user, content=content, chars=len(content))
    stale = list(project.snapshots.filter(user=user).values_list("pk", flat=True)[SNAPSHOTS_KEPT:])
    if stale:
        Snapshot.objects.filter(pk__in=stale).delete()
    return saved


def summarise(events) -> dict:
    """Totals a reviewer can read: how much was typed, how much pasted, whence."""
    total = {"typed": 0, "pasted": 0, "pastes": 0, "ai_candidate": 0, "own_paper": 0,
             "outside": 0, "unknown": 0}
    for event in events:
        if event.kind == CompositionEvent.Kind.TYPING:
            total["typed"] += event.chars
        else:
            total["pasted"] += event.chars
            total["pastes"] += 1
            total[event.source or "unknown"] = total.get(event.source or "unknown", 0) + 1
    written = total["typed"] + total["pasted"]
    total["typed_pct"] = round(100 * total["typed"] / written) if written else 0
    total["pasted_pct"] = 100 - total["typed_pct"] if written else 0
    total["any"] = bool(written)
    return total


def for_commit(project, sha: str) -> dict:
    return summarise(project.composition.filter(commit_sha=sha))


def for_unit(project, unit_id: str) -> dict:
    return summarise(project.composition.filter(unit_id=unit_id))


def pending(project):
    return project.composition.filter(commit_sha="")


def trailer(project) -> str:
    """The one-line summary that rides along with a commit, or '' if nothing."""
    total = summarise(pending(project))
    if not total["any"]:
        return ""
    parts = [f"typed={total['typed']}", f"pasted={total['pasted']}", f"pastes={total['pastes']}"]
    for source in ("ai_candidate", "own_paper", "outside", "unknown"):
        if total.get(source):
            parts.append(f"{source}={total[source]}")
    return " ".join(parts)


def attach(project, sha: str) -> int:
    return pending(project).update(commit_sha=sha)
