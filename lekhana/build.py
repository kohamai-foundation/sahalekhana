"""Building the paper out of the portal: a PDF, and an export anyone can read.

**PDF.** ``latexmk`` (or whatever ``LEKHANA_LATEX_CMD`` names) runs over a copy
of the repository in a temporary directory, so a failed build cannot leave
artefacts in the record. TeX is not in the image by default — the Dockerfile
takes ``--build-arg INSTALL_TEXLIVE=1`` — and the portal says so plainly when
it is missing rather than pretending the button is broken.

**Export.** A zip holding the source, the flattened source (portal macros
resolved, for a publisher's template), the PDF when one was built, the full
provenance record, and a declaration of AI use generated from the ledger rather
than written from memory. ``anonymise`` replaces names with stable role labels
for a double-blind submission.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

from . import composition, grammar, latex, ledger
from .models import Act, Ask, Candidate, Conversation, Idea, MethodRun, ReviewPoint


def latex_command() -> list[str]:
    return (settings.LEKHANA_LATEX_CMD or "").split()


def latex_available() -> bool:
    command = latex_command()
    return bool(command) and shutil.which(command[0]) is not None


class BuildError(RuntimeError):
    """The PDF could not be produced. The message is written for the writer."""


def compile_pdf(project) -> tuple[bytes, str]:
    """Returns (pdf bytes, the build log). Raises BuildError with the log's tail
    when LaTeX fails, which for an author is usually the useful part."""
    from . import services
    if not latex_available():
        raise BuildError(
            "No LaTeX on this server, so the PDF can't be built here. The source and the export still work; "
            "rebuild the image with --build-arg INSTALL_TEXLIVE=1 to enable it.")
    source = services.source(project)
    if not source.strip():
        raise BuildError("There is nothing to compile yet.")
    with tempfile.TemporaryDirectory(prefix="lekhana-build-") as work:
        folder = Path(work)
        (folder / latex.MAIN).write_text(source, encoding="utf-8")
        (folder / latex.STY_NAME).write_text(latex.STY, encoding="utf-8")
        try:
            done = subprocess.run([*latex_command(), latex.MAIN], cwd=folder, capture_output=True,
                                  text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise BuildError("LaTeX took too long and was stopped after two minutes.") from exc
        log = (done.stdout or "") + (done.stderr or "")
        pdf = folder / "main.pdf"
        if done.returncode != 0 or not pdf.exists():
            tail = "\n".join(line for line in log.splitlines() if line.strip())[-1500:]
            raise BuildError(f"LaTeX could not build the paper.\n\n{tail}")
        return pdf.read_bytes(), log[-4000:]


# ── the record, for someone outside the portal ───────────────────────────────

def _anonymiser(project, on: bool):
    """Stable role labels instead of names: author-1, reviewer-1, ai."""
    if not on:
        return lambda agent: agent
    mapping, counts = {}, {"author": 0, "reviewer": 0}
    for membership in project.memberships.select_related("user").order_by("role", "created_at"):
        counts[membership.role] = counts.get(membership.role, 0) + 1
        mapping[f"human:{membership.user.get_username()}"] = f"{membership.role}-{counts[membership.role]}"

    def label(agent: str) -> str:
        if agent.startswith("ai:"):
            return "ai"
        return mapping.get(agent, "person")
    return label


def provenance(project, *, anonymise: bool = False) -> dict:
    """Everything the ledger knows, as one document."""
    label = _anonymiser(project, anonymise)
    acts = []
    for act in project.acts.select_related("candidate").order_by("id"):
        acts.append({"kind": act.kind, "agent": label(act.agent), "role": act.role, "yukti": act.yukti,
                     "unit": act.unit_id, "meaning_bearing": act.meaning_bearing, "defect": act.defect,
                     "commit": act.commit_sha, "at": act.created_at.isoformat(),
                     "note": "" if anonymise else act.note})
    return {
        "paper": {"title": project.title, "slug": project.slug,
                  "head": ledger.head(ledger.repo_path(project.slug)) or ""},
        "acts": acts,
        "asks": [a.to_record() for a in Ask.objects.filter(project=project).prefetch_related("candidates")],
        "review_points": [p.to_record() for p in ReviewPoint.objects.filter(project=project)],
        "ideas": [i.to_record() for i in Idea.objects.filter(project=project)],
        "methods": [m.to_record() for m in MethodRun.objects.filter(project=project).prefetch_related("steps")],
        "discussions": [c.to_record() for c in Conversation.objects.filter(project=project).prefetch_related("turns")],
        "composition": {
            "tracking": project.track_composition,
            "total": composition.summarise(project.composition.all()),
            "events": [{"kind": e.kind, "unit": e.unit_id, "chars": e.chars, "source": e.source,
                        "hash": e.text_hash, "candidate": e.matched_candidate_id,
                        "commit": e.commit_sha, "at": e.created_at.isoformat()}
                       for e in project.composition.all()],
        },
    }


def declaration(project) -> str:
    """The AI-use declaration, generated from the ledger instead of memory."""
    from . import services
    rows, unexamined = services.profile(project)
    acts = project.acts.all()
    candidates = Candidate.objects.filter(ask__project=project, ask__purpose=Ask.Purpose.WRITE)
    accepted = candidates.filter(status=Candidate.Status.ACCEPTED)
    rejected = candidates.filter(status=Candidate.Status.REJECTED)
    ai_ideas = Idea.objects.filter(project=project, origin__startswith="ai:").count()
    confirmed_ai = Idea.objects.filter(project=project, origin__startswith="ai:",
                                       confirmed_at__isnull=False).count()
    try:
        owed = len(latex.parse_owed(services.source(project)))
    except latex.LatexError:
        owed = 0
    models = sorted({a.agent[3:] for a in acts if a.is_ai and a.agent})
    lines = [
        f"# Declaration on the use of generative AI — {project.title}",
        "",
        "Generated from this paper's act record, not written from memory. "
        f"Model(s) used: {', '.join(models) or 'none'}.",
        "",
        "## What each side did",
        "",
        "| Kind of act | People | AI |",
        "|---|---:|---:|",
    ]
    lines += [f"| {row['family']} | {row['human']} | {row['ai']} |" for row in rows]
    lines += [
        "",
        f"- AI candidates offered: {candidates.count()}; accepted after examination: "
        f"{accepted.filter(examined_at__isnull=False).count()}; accepted unexamined: "
        f"{accepted.filter(examined_at__isnull=True).count()}; rejected: {rejected.count()}.",
    ]
    if rejected.exists():
        reasons = ", ".join(sorted({c.reason_label for c in rejected if c.reason}))
        lines.append(f"- Grounds given for rejection: {reasons}.")
    lines += [
        f"- Ideas or framings recorded as the AI's: {ai_ideas} ({confirmed_ai} confirmed by an author).",
        f"- Slots still owed by the authors: {owed}.",
    ]
    if unexamined:
        lines.append(f"- **{unexamined} AI candidate(s) were accepted without examination.**")
    composed = composition.summarise(project.composition.all())
    if composed["any"]:
        lines.append(
            f"- How the text arrived in the editor: {composed['typed_pct']}% typed, "
            f"{composed['pasted_pct']}% pasted across {composed['pastes']} paste(s) "
            f"({composed['ai_candidate']} from the AI's candidates, {composed['own_paper']} from "
            f"elsewhere in the paper, {composed['outside']} from outside the portal).")
    elif not project.track_composition:
        lines.append("- Composition tracking (typed vs pasted) is off for this paper.")
    lines += [
        "",
        "The AI never wrote to the paper: every sentence in it was placed there by an author, "
        "and each acceptance is recorded with whether the author examined the candidate first.",
        "",
        "The full act-level record can be released to an editor or integrity panel on request.",
    ]
    return "\n".join(lines) + "\n"


def export_zip(project, *, flatten: bool = True, anonymise: bool = False, pdf: bytes | None = None) -> bytes:
    from . import services
    source = services.source(project)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{project.slug}/main.tex", source)
        bundle.writestr(f"{project.slug}/{latex.STY_NAME}", latex.STY)
        if flatten:
            bundle.writestr(f"{project.slug}/main-plain.tex", latex.flatten(source))
        if pdf:
            bundle.writestr(f"{project.slug}/main.pdf", pdf)
        bundle.writestr(f"{project.slug}/DECLARATION.md", declaration(project))
        bundle.writestr(f"{project.slug}/provenance.json",
                        json.dumps(provenance(project, anonymise=anonymise), ensure_ascii=False,
                                   indent=2, sort_keys=True) + "\n")
        bundle.writestr(f"{project.slug}/README.txt", _readme(project, flatten, anonymise))
    return buffer.getvalue()


def _readme(project, flatten: bool, anonymise: bool) -> str:
    return (
        f"{project.title}\nExported from Sahalekhana.\n\n"
        "main.tex        the paper, with the portal's markup (\\yukti, \\owed, \\adhikarana, \\ask)\n"
        + ("main-plain.tex  the same paper with the markup resolved, for a publisher's template\n" if flatten else "")
        + "DECLARATION.md  declaration of AI use, generated from the act record\n"
        "provenance.json every recorded act, request, idea, review point and discussion\n"
        + ("\nNames are replaced by role labels (author-1, reviewer-1, ai) in this export.\n" if anonymise else "")
    )


def record(project, user, *, kind: str, note: str) -> None:
    """Compiling and exporting are acts too: they say the paper left the portal."""
    from . import services
    Act.objects.create(project=project, user=user, agent=services.human(user),
                       role="svatantra", kind=kind, note=note)
    with ledger.lock(services.repo(project)):
        services.commit_records(project, user, message=note)
