"""The co-writing portal: server-rendered, like the other benches on this site.

Every POST goes through ``lekhana.services`` so each change is recorded as acts
and committed to the project's git repository. Authors write; reviewers read
the paper with its making, ask the paper, and raise points in the same space.
"""
import functools
import json
from collections import Counter
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required as _login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from . import ai, build, chat, composition, grammar, latex, ledger, methods, services
from .models import Anchor, Ask, Candidate, Conversation, Idea, MethodStep, Project, ReviewPoint

login_required = functools.partial(_login_required, login_url="/accounts/login/")

_FAILURES = (services.InvalidAction, services.Conflict, latex.LatexError, ledger.LedgerError)


def _project(request, slug) -> Project:
    project = get_object_or_404(Project, slug=slug)
    if not services.can_access(request.user, project):
        raise Http404
    return project


def _writable(request, project) -> None:
    if not services.can_write(request.user, project):
        raise PermissionDenied("Reviewers can read, ask and comment, but not edit the paper.")


def _to(slug, anchor=""):
    return redirect(reverse("lekhana:project", args=[slug]) + (f"#{anchor}" if anchor else ""))


def _to_review(slug, unit="", anchor=""):
    query = f"?{urlencode({'unit': unit})}" if unit else ""
    return redirect(reverse("lekhana:review", args=[slug]) + query + (f"#{anchor}" if anchor else ""))


@login_required
def index(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        slug = slugify(request.POST.get("slug", "").strip() or title)[:50]
        if not title or not slug:
            messages.error(request, "Give the paper a title.")
            return redirect("lekhana:index")
        try:
            project = services.create_project(user=request.user, title=title, slug=slug)
        except (services.InvalidAction, ledger.LedgerError) as exc:
            messages.error(request, str(exc))
            return redirect("lekhana:index")
        messages.success(request, f"Created {project.title}. Its first commit is in the record.")
        return redirect("lekhana:project", slug=project.slug)
    mine = {m.project_id: m.get_role_display() for m in request.user.lekhana_memberships.all()}
    projects = Project.objects.all() if request.user.is_staff else Project.objects.filter(pk__in=mine)
    rows = [(p, mine.get(p.pk, "Staff")) for p in projects.order_by("-created_at")]
    return render(request, "lekhana/index.html", {"projects": rows})


def _workspace(request, project, content=None) -> dict:
    text = services.source(project) if content is None else content
    parse_error = ""
    try:
        units = latex.parse_units(text)
        owed = latex.parse_owed(text)
        sections = latex.parse_sections(text)
    except latex.LatexError as exc:
        units, owed, sections, parse_error = [], [], [], str(exc)
    outline = sorted(
        [{"type": "section", "line": s.line, "title": s.title} for s in sections]
        + [{"type": "unit", "line": u.line, "id": u.id, "iast": grammar.iast(u.yukti),
            "snippet": " ".join(latex.to_plain(u.body).split())[:60]} for u in units],
        key=lambda item: item["line"])
    return {
        "project": project, "tab": "write", "role": services.role_of(request.user, project),
        "source": text, "head": services.head(project), "outline": outline,
        "unit_ids": [u.id for u in units], "owed": owed, "parse_error": parse_error,
        "asks": project.asks.filter(purpose=Ask.Purpose.WRITE).select_related("requested_by")
                       .prefetch_related("candidates__decided_by")[:25],
        "pending": project.acts.filter(commit_sha="").count(),
        "devices": grammar.ASK_DEVICES,
        "defect_reasons": [r for r in grammar.REJECT_REASONS if r.defect],
        "other_reasons": [r for r in grammar.REJECT_REASONS if not r.defect],
        "can_write": True, "ai_configured": ai.configured(),
        "tracking": project.track_composition,
    }


@login_required
def project(request, slug):
    project = _project(request, slug)
    if not services.can_write(request.user, project):
        return _to_review(slug)
    return render(request, "lekhana/project.html", _workspace(request, project))


@login_required
@require_POST
def save(request, slug):
    project = _project(request, slug)
    _writable(request, project)
    content = latex.normalize(request.POST.get("content", ""))
    base_sha = request.POST.get("base_sha", "")

    def back_to_editor(error, status):
        messages.error(request, error)
        ctx = _workspace(request, project, content)
        ctx["head"], ctx["unsaved"] = base_sha, True
        return render(request, "lekhana/project.html", ctx, status=status)

    if request.POST.get("step") == "commit":
        try:
            sha = services.save(project=project, user=request.user, content=content, base_sha=base_sha,
                                message=request.POST.get("message", ""),
                                meaning=set(request.POST.getlist("meaning")))
        except services.Conflict as exc:
            return back_to_editor(str(exc), 409)
        except _FAILURES as exc:
            return back_to_editor(str(exc), 400)
        messages.success(request, f"Committed {sha[:7]}.")
        return _to(slug)

    try:
        found = services.review_changes(project, content)
        new_owed = latex.added_owed(services.source(project), content)
    except latex.LatexError as exc:
        return back_to_editor(f"The source didn't parse, so nothing was saved: {exc}", 400)
    if not found:
        messages.info(request, "Nothing changed since the last commit.")
        return _to(slug)
    return render(request, "lekhana/commit_review.html", {
        "project": project, "tab": "write", "changes": found, "content": content,
        "base_sha": base_sha, "new_owed": new_owed})


@login_required
@require_POST
def ask_view(request, slug):
    project = _project(request, slug)
    _writable(request, project)
    try:
        thread = services.ask(
            project=project, user=request.user, unit_id=request.POST.get("unit_id", ""),
            device=request.POST.get("device", ""), count=request.POST.get("count", 3),
            instruction=request.POST.get("instruction", ""), constraint=request.POST.get("constraint", ""))
    except services.InvalidAction as exc:
        messages.error(request, str(exc))
        return _to(slug)
    if thread.status == Ask.Status.FAILED:
        messages.error(request, thread.error)
    else:
        n = thread.candidates.count()
        messages.success(request, f"{n} candidate{'s' if n != 1 else ''} from {thread.model}. "
                                  "Examine each before you accept it.")
    return _to(slug, f"ask-{thread.pk}")


@login_required
@require_POST
def candidate(request, slug, pk):
    project = _project(request, slug)
    _writable(request, project)
    cand = get_object_or_404(Candidate.objects.select_related("ask"), pk=pk, ask__project=project)
    action = request.POST.get("action")
    try:
        if action == "examine":
            services.examine(cand, request.user)
        elif action == "accept":
            sha = services.accept(cand, request.user)
            how = "after examining it" if cand.examined_at else "without examining it (recorded as such)"
            messages.success(request, f"Accepted ({cand.letter}) {how}. Committed {sha[:7]}.")
        elif action == "reject":
            services.reject(cand, request.user, reason=request.POST.get("reason", ""),
                            note=request.POST.get("note", ""))
            messages.success(request, f"Rejected ({cand.letter}). The reason goes into the next commit.")
        else:
            raise services.InvalidAction("Unknown action.")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return _to(slug, f"ask-{cand.ask_id}")


# ── reviewing ────────────────────────────────────────────────────────────────

_EXTRA_HANDLES = [("ai", "AI-proposed"), ("unexamined", "Accepted without examining"), ("other", "Other devices")]


def _matches(unit, handle) -> bool:
    if handle == "ai":
        return unit["origin"].startswith("ai")
    if handle == "unexamined":
        return unit["origin"] == "ai_unexamined"
    return unit["family"] == handle


@login_required
def review(request, slug):
    project = _project(request, slug)
    view = request.GET.get("view", "annotated")
    view = view if view in ("annotated", "plain") else "annotated"
    handle = request.GET.get("handle", "")
    selected_unit = request.GET.get("unit", "")
    text = services.source(project)
    parse_error = ""
    try:
        blocks = services.paper_blocks(text, services.unit_origins(project))
    except latex.LatexError as exc:
        blocks, parse_error = [], str(exc)

    counts, unit_ids = Counter(), set()
    for block in blocks:
        for unit in block.get("units", []):
            unit_ids.add(unit["id"])
            unit["matches"] = _matches(unit, handle) if handle else True
            counts[unit["family"]] += 1
            counts["ai"] += unit["origin"].startswith("ai")
            counts["unexamined"] += unit["origin"] == "ai_unexamined"
    handles = [{"code": code, "label": label, "count": counts[code]}
               for code, label in list(grammar.FAMILIES.items()) + _EXTRA_HANDLES if counts[code]]
    if selected_unit not in unit_ids:
        selected_unit = ""

    points = project.review_points.select_related("author", "responded_by")
    review_asks = project.asks.filter(purpose=Ask.Purpose.REVIEW).select_related("requested_by") \
        .prefetch_related("candidates")
    if selected_unit:
        points = points.filter(unit_id=selected_unit)
        review_asks = review_asks.filter(unit_id=selected_unit)
    return render(request, "lekhana/review.html", {
        "project": project, "tab": "review", "role": services.role_of(request.user, project),
        "view": view, "handle": handle, "handles": handles, "blocks": blocks, "parse_error": parse_error,
        "selected_unit": selected_unit,
        "selected": services.unit_history(project, selected_unit) if selected_unit else None,
        "composition": composition.for_unit(project, selected_unit) if selected_unit else None,
        "tracking": project.track_composition,
        "points": points[:50], "review_asks": review_asks[:15],
        "review_devices": grammar.REVIEW_DEVICES, "point_kinds": grammar.REVIEW_POINT_KINDS,
        "can_write": services.can_write(request.user, project),
        "members": project.memberships.select_related("user"),
        "ai_configured": ai.configured(), "act_label": grammar.ACT_LABEL,
    })


@login_required
@require_POST
def review_ask_view(request, slug):
    project = _project(request, slug)
    unit = request.POST.get("unit_id", "").strip()
    try:
        thread = services.review_ask(project=project, user=request.user, unit_id=unit,
                                     device=request.POST.get("device", ""),
                                     instruction=request.POST.get("instruction", ""),
                                     count=request.POST.get("count", 3))
    except _FAILURES as exc:
        messages.error(request, str(exc))
        return _to_review(slug, unit)
    if thread.status == Ask.Status.FAILED:
        messages.error(request, thread.error)
    else:
        messages.success(request, f"The paper answered ({thread.model}). The exchange is committed.")
    return _to_review(slug, thread.unit_id, f"ask-{thread.pk}")


@login_required
@require_POST
def point_view(request, slug):
    project = _project(request, slug)
    unit = request.POST.get("unit_id", "").strip()
    try:
        point = services.add_point(project=project, user=request.user, unit_id=unit,
                                   kind=request.POST.get("kind", ""), text=request.POST.get("text", ""))
    except _FAILURES as exc:
        messages.error(request, str(exc))
        return _to_review(slug, unit)
    messages.success(request, "Point recorded and committed.")
    return _to_review(slug, point.unit_id, f"point-{point.pk}")


@login_required
@require_POST
def respond_view(request, slug, pk):
    project = _project(request, slug)
    _writable(request, project)
    point = get_object_or_404(ReviewPoint, pk=pk, project=project)
    try:
        services.respond(point, request.user, text=request.POST.get("text", ""))
        messages.success(request, "Response recorded and committed.")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return _to_review(slug, point.unit_id, f"point-{point.pk}")


@login_required
@require_POST
def grant_view(request, slug):
    project = _project(request, slug)
    _writable(request, project)
    try:
        m = services.grant(project=project, user=request.user, username=request.POST.get("username", ""),
                           role=request.POST.get("role", ""))
        messages.success(request, f"{m.user.get_username()} is now a {m.role}. The grant is committed.")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return redirect("lekhana:ledger", slug=slug)


# ── discussion, ideas, methods ───────────────────────────────────────────────

@login_required
def discuss(request, slug):
    project = _project(request, slug)
    conversations = project.conversations.select_related("created_by")
    current = None
    if request.GET.get("c"):
        current = get_object_or_404(Conversation, pk=request.GET["c"], project=project)
    elif conversations:
        current = conversations.first()
    source = services.source(project)
    return render(request, "lekhana/discuss.html", {
        "project": project, "tab": "discuss", "role": services.role_of(request.user, project),
        "conversations": conversations, "current": current,
        "turns": current.turns.select_related("user") if current else [],
        "stances": grammar.STANCES,
        "stance": grammar.STANCE.get(current.stance) if current else None,
        "ideas": project.ideas.select_related("confirmed_by", "origin_turn")[:60],
        "idea_kinds": grammar.IDEA_KINDS,
        "methods": methods.METHODS,
        "runs": [{"run": run, "steps": methods.progress(run, source)}
                 for run in project.method_runs.prefetch_related("steps")[:5]],
        "can_write": services.can_write(request.user, project),
        "ai_configured": ai.configured(),
    })


def _to_discuss(slug, conversation=None, anchor=""):
    query = f"?c={conversation.pk}" if conversation else ""
    return redirect(reverse("lekhana:discuss", args=[slug]) + query + (f"#{anchor}" if anchor else ""))


@login_required
@require_POST
def discuss_new(request, slug):
    project = _project(request, slug)
    stance = request.POST.get("stance", grammar.DEFAULT_STANCE)
    if stance not in grammar.STANCE:
        stance = grammar.DEFAULT_STANCE
    conversation = Conversation.objects.create(
        project=project, title=request.POST.get("title", "").strip()[:120],
        stance=stance, created_by=request.user)
    return _to_discuss(slug, conversation)


@login_required
@require_POST
def discuss_send(request, slug, pk):
    project = _project(request, slug)
    conversation = get_object_or_404(Conversation, pk=pk, project=project)
    try:
        turn = chat.send(conversation=conversation, user=request.user, text=request.POST.get("text", ""))
    except _FAILURES as exc:
        messages.error(request, str(exc))
        return _to_discuss(slug, conversation)
    if turn.flagged:
        messages.error(request, f"The reply broke the role you set: it {turn.flagged}. "
                                "It is recorded as it came, and flagged.")
    return _to_discuss(slug, conversation, f"turn-{turn.pk}")


@login_required
@require_POST
def discuss_stance(request, slug, pk):
    project = _project(request, slug)
    conversation = get_object_or_404(Conversation, pk=pk, project=project)
    try:
        chat.set_stance(conversation=conversation, user=request.user, stance=request.POST.get("stance", ""))
        messages.success(request, f"The AI's role is now {conversation.get_stance_display().lower()}.")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return _to_discuss(slug, conversation)


@login_required
@require_POST
def idea_attribute(request, slug, pk):
    project = _project(request, slug)
    idea = get_object_or_404(Idea, pk=pk, project=project)
    try:
        chat.attribute(idea=idea, user=request.user, origin=request.POST.get("origin", ""),
                       status=request.POST.get("status", ""))
        messages.success(request, "Attribution recorded." if not idea.corrected else "Attribution corrected.")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return _to_discuss(slug, None, f"idea-{idea.pk}")


@login_required
@require_POST
def method_start(request, slug):
    project = _project(request, slug)
    _writable(request, project)
    try:
        run = methods.start(project=project, user=request.user, code=request.POST.get("code", ""),
                            target=request.POST.get("target", ""), note=request.POST.get("note", ""))
        messages.success(request, f"{methods.METHOD[run.code].label}: {run.steps.count()} steps laid out. "
                                  "The writing is yours; the portal tracks the steps.")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return _to_discuss(slug)


@login_required
@require_POST
def method_step(request, slug, pk):
    project = _project(request, slug)
    _writable(request, project)
    step = get_object_or_404(MethodStep, pk=pk, run__project=project)
    try:
        methods.mark(step=step, user=request.user, done=request.POST.get("done") == "1")
    except _FAILURES as exc:
        messages.error(request, str(exc))
    return _to_discuss(slug)


# ── how the text arrived ─────────────────────────────────────────────────────

@login_required
@require_POST
def compose(request, slug):
    """The editor reports characters typed and, per paste, a length and a hash.
    The pasted text itself stays in the browser."""
    project = _project(request, slug)
    _writable(request, project)
    if not project.track_composition:
        return JsonResponse({"tracking": False})
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("expected JSON")
    made = composition.record(project=project, user=request.user,
                              events=payload.get("events") or [],
                              source_text=services.source(project))
    if payload.get("snapshot"):
        composition.snapshot(project=project, user=request.user,
                             content=latex.normalize(str(payload["snapshot"])[:400_000]))
    return JsonResponse({"tracking": True, "recorded": len(made),
                         "sources": [e.source for e in made if e.kind == "paste"]})


@login_required
@require_POST
def tracking(request, slug):
    project = _project(request, slug)
    _writable(request, project)
    on = request.POST.get("on") == "1"
    services.set_tracking(project=project, user=request.user, on=on)
    messages.success(request, f"Composition tracking is {'on' if on else 'off'}. The change is in the record.")
    return redirect("lekhana:ledger", slug=slug)


# ── out of the portal: PDF and export ────────────────────────────────────────

@login_required
def pdf(request, slug):
    project = _project(request, slug)
    try:
        content, _log = build.compile_pdf(project)
    except build.BuildError as exc:
        messages.error(request, str(exc))
        return redirect("lekhana:ledger", slug=slug)
    build.record(project, request.user, kind="compile", note="Compiled the PDF")
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{project.slug}.pdf"'
    return response


@login_required
def export(request, slug):
    project = _project(request, slug)
    anonymise = request.GET.get("anonymise") == "1"
    content = None
    if request.GET.get("pdf") == "1" and build.latex_available():
        try:
            content, _log = build.compile_pdf(project)
        except build.BuildError as exc:
            messages.error(request, f"Exported without a PDF: {exc}")
    bundle = build.export_zip(project, flatten=True, anonymise=anonymise, pdf=content)
    build.record(project, request.user, kind="export",
                 note="Exported the paper" + (" (anonymised)" if anonymise else ""))
    response = HttpResponse(bundle, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{project.slug}-export.zip"'
    return response


@login_required
def ledger_view(request, slug):
    project = _project(request, slug)
    rows, unexamined = services.profile(project)
    try:
        owed = latex.parse_owed(services.source(project))
    except latex.LatexError:
        owed = []
    entries = ledger.log(ledger.repo_path(project.slug))
    for entry in entries:
        for act in entry["acts"]:
            act["label"] = grammar.ACT_LABEL.get(act["kind"], act["kind"])
        entry["compose"] = next((v for k, v in entry["trailers"] if k == "Compose"), "")
    anchors = [a for a in Anchor.objects.all()[:200] if any(s == project.slug for s, _ in a.heads)][:10]
    return render(request, "lekhana/ledger.html", {
        "project": project, "tab": "ledger", "role": services.role_of(request.user, project),
        "entries": entries,
        "pending": project.acts.filter(commit_sha="").select_related("candidate"),
        "profile": rows, "unexamined": unexamined, "owed": owed, "anchors": anchors,
        "members": project.memberships.select_related("user", "granted_by"),
        "member_roles": grammar.MEMBER_ROLES,
        "can_write": services.can_write(request.user, project),
        "latex_available": build.latex_available(),
        "tracking": project.track_composition,
        "composition": composition.summarise(project.composition.all()),
    })
