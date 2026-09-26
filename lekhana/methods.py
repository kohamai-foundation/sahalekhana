"""Ways of working: the writer picks a method, the portal lays out its steps.

A method never writes prose. It prepares the structure — sections, empty units,
placement markers — and tracks which steps the writer has filled in. The
writing stays the writer's; what the AI may do at each step is whatever the
discussion's stance allows.

``abstract_expansion`` is the one the user asked for: take the abstract, split
it into sentences, and give each sentence its own section and empty unit to be
expanded into.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction

from . import grammar, latex, ledger
from .models import Act, MethodRun, MethodStep


@dataclass(frozen=True)
class Method:
    code: str
    label: str
    summary: str
    needs_unit: bool
    unit_hint: str = ""


METHODS = [
    Method("abstract_expansion", "Expand the abstract, sentence by sentence",
           "Each sentence of the abstract becomes a section with an empty unit for you to expand into. "
           "One step per sentence, ticked off as you write it.",
           needs_unit=True, unit_hint="the unit holding the abstract, e.g. abstract"),
    Method("adhikarana_section", "Lay out a section in five limbs",
           "Topic, doubt, the opposing view, the reply, the decision — each as its own placement marker.",
           needs_unit=False, unit_hint="a short name for the section, e.g. construe"),
    Method("pancavayava", "Lay out a five-part argument",
           "Thesis, reason, example, application, conclusion — the Nyāya sequence, as five markers.",
           needs_unit=False, unit_hint="a short name for the argument, e.g. why-anvaya"),
]
METHOD = {m.code: m for m in METHODS}

_LIMBS = {
    "adhikarana_section": [("vishaya", "adhikarana", "Topic: what this section is about"),
                           ("samshaya", "samshaya", "Doubt: what is genuinely open"),
                           ("purvapaksha", "purvapaksha", "The opposing view, at full strength"),
                           ("uttarapaksha", "uttarapaksha", "The reply"),
                           ("nirnaya", "samuccaya", "The decision, and what stays open")],
    "pancavayava": [("pratijna", "uddesha", "Thesis: the claim"),
                    ("hetu", "hetvartha", "Reason: the warrant"),
                    ("udaharana", "nidarshana", "Example: a case where reason and claim go together"),
                    ("upanaya", "atidesha", "Application: and so it is here"),
                    ("nigamana", "arthapatti", "Conclusion: the claim, now established")],
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    plain = " ".join(latex.to_plain(text).split())
    return [s.strip() for s in _SENTENCE.split(plain) if s.strip()]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "-", value.strip()).strip("-.:")
    return (cleaned or "part")[:32]


def _seed_abstract(source: str, base: str, sentences: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """A section and an empty unit per abstract sentence, appended in order."""
    steps, additions = [], []
    for i, sentence in enumerate(sentences, 1):
        unit_id = f"{base}.s{i}"
        title = sentence if len(sentence) <= 60 else sentence[:57].rsplit(" ", 1)[0] + "…"
        additions.append(
            f"\\adhikarana{{sec:{base}.s{i}}}{{{latex.latex_escape(title)}}}\n"
            f"% from the abstract, sentence {i}: {latex.latex_escape(sentence)}\n"
            f"{latex.unit_command(unit_id, 'nirdesha', '')}\n")
        steps.append((unit_id, sentence))
    return _append(source, "\n".join(additions)), steps


def _seed_limbs(source: str, base: str, code: str) -> tuple[str, list[tuple[str, str]]]:
    steps, additions = [], [f"\\adhikarana{{sec:{base}}}{{{latex.latex_escape(base)}}}\n"]
    for suffix, _device, label in _LIMBS[code]:
        unit_id = f"{base}.{suffix}"
        additions.append(f"% {label}\n\\ask{{{unit_id}}}\n")
        steps.append((unit_id, label))
    return _append(source, "".join(additions)), steps


def _append(source: str, text: str) -> str:
    end = source.find("\\end{document}")
    if end == -1:
        return source.rstrip("\n") + "\n\n" + text
    return source[:end] + text + "\n" + source[end:]


def start(*, project, user, code: str, target: str, note: str = "") -> MethodRun:
    """Lay out the method's steps and seed the source. One commit, one act."""
    from . import services
    method = METHOD.get(code)
    if method is None:
        raise services.InvalidAction("Choose a way of working.")
    target = target.strip()
    path = services.repo(project)
    with ledger.lock(path):
        source = ledger.read(path, latex.MAIN)
        if method.code == "abstract_expansion":
            unit = {u.id: u for u in latex.parse_units(source)}.get(target)
            if unit is None:
                raise services.InvalidAction(
                    f"No unit {target!r} to expand. Write the abstract as a unit first, then start this method.")
            sentences = split_sentences(unit.body)
            if not sentences:
                raise services.InvalidAction("That unit has no sentences yet.")
            new_source, steps = _seed_abstract(source, _slug(target), sentences)
        else:
            base = _slug(target)
            if not base:
                raise services.InvalidAction("Give the section a short name.")
            new_source, steps = _seed_limbs(source, base, method.code)
        latex.parse_units(new_source)
        with transaction.atomic():
            run = MethodRun.objects.create(project=project, code=method.code, created_by=user, note=note.strip())
            for i, (unit_id, label) in enumerate(steps, 1):
                MethodStep.objects.create(run=run, index=i, label=label[:300], unit_id=unit_id)
            Act.objects.create(project=project, user=user, agent=services.human(user), role="svatantra",
                               kind="method_start", note=f"{method.code}:{target} ({len(steps)} steps)")
            services._commit(project, user, files={latex.MAIN: new_source},
                             message=f"{method.label}: {len(steps)} steps on {target}")
    return run


def progress(run: MethodRun, source: str) -> list[dict]:
    """A step counts as done when its unit exists with text in it, or when the
    writer marked it done. The portal reads the paper; it never writes it."""
    units = {}
    try:
        units = {u.id: u for u in latex.parse_units(source)}
    except latex.LatexError:
        pass
    rows = []
    for step in run.steps.all():
        unit = units.get(step.unit_id)
        written = bool(unit and unit.body.strip())
        rows.append({"step": step, "written": written, "done": step.done or written,
                     "words": len(unit.body.split()) if unit else 0})
    return rows


def mark(*, step: MethodStep, user, done: bool = True) -> MethodStep:
    from . import services
    from django.utils import timezone
    project = step.run.project
    step.done = done
    step.done_at = timezone.now() if done else None
    step.save(update_fields=["done", "done_at"])
    Act.objects.create(project=project, user=user, agent=services.human(user), role="svatantra",
                       kind="method_step", unit_id=step.unit_id,
                       note=f"{step.run.code} step {step.index}: {'done' if done else 'reopened'}")
    with ledger.lock(services.repo(project)):
        services.commit_records(project, user, message=f"Method step {step.index} on {step.unit_id or step.run.code}")
    return step
