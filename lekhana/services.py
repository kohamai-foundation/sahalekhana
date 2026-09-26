"""Everything that changes a project goes through here, so each change is
recorded as typed acts and carried into git in one place (:func:`_commit`).

Writing (authors):
* :func:`save` — the writer's own edits, confirmed unit by unit.
* :func:`ask` — the writer directs the AI (prayojaka); the AI proposes (prayojya).
* :func:`examine` / :func:`accept` / :func:`reject` — the writer as examiner (sākṣin).

Reviewing (reviewers, and authors reading as reviewers):
* :func:`review_ask` — ask the paper; answers are recorded, never placed.
* :func:`add_point` / :func:`respond` — a review point and the author's response.
* :func:`unit_history` — how one unit came to be: every version, every act.

Access: :func:`grant` — an author gives someone an adhikāra on the paper.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import ai, composition, grammar, latex, ledger
from .models import Act, Ask, Candidate, Conversation, Membership, Project, ReviewPoint


class Conflict(Exception):
    """The paper changed since the writer loaded the page."""


class InvalidAction(Exception):
    """The request can't be carried out. The message is written for the person."""


def human(user) -> str:
    return f"human:{user.get_username()}"


def role_of(user, project: Project) -> str:
    """author | reviewer | staff | "" (no access)."""
    m = project.memberships.filter(user=user).first()
    if m:
        return m.role
    return "staff" if user.is_staff else ""


def can_access(user, project: Project) -> bool:
    return bool(role_of(user, project))


def can_write(user, project: Project) -> bool:
    return role_of(user, project) in ("author", "staff")


def _review_role(user, project: Project) -> str:
    return "parikshaka" if role_of(user, project) == "reviewer" else "sakshin"


def _identity(user) -> tuple[str, str]:
    name = user.get_full_name() or user.get_username()
    email = user.email or f"{user.get_username()}@users.heritagesemantics.org"
    return name, email


def _json(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _commit(project: Project, user, *, files: dict[str, str], message: str) -> str:
    """Commit ``files`` with every pending act on the project as trailers, plus the
    current state of every thread and review point those acts belong to.
    Callers hold :func:`ledger.lock` (except on a brand-new repository)."""
    pending = list(project.acts.filter(commit_sha="").select_related("candidate").order_by("id"))
    files = dict(files)
    ask_ids = {a.ask_id for a in pending if a.ask_id}
    for thread in Ask.objects.filter(pk__in=ask_ids).select_related("requested_by").prefetch_related(
            "candidates__examined_by", "candidates__decided_by"):
        files[f"{ledger.RECORD_DIR}/vimarsha/{thread.pk}.json"] = _json(thread.to_record())
    point_ids = {a.review_point_id for a in pending if a.review_point_id}
    for point in ReviewPoint.objects.filter(pk__in=point_ids).select_related("author", "responded_by"):
        files[f"{ledger.RECORD_DIR}/review/{point.pk}.json"] = _json(point.to_record())
    talk_ids = {a.conversation_id for a in pending if a.conversation_id}
    for talk in Conversation.objects.filter(pk__in=talk_ids).prefetch_related("turns__user"):
        files[f"{ledger.RECORD_DIR}/samvada/{talk.pk}.json"] = _json(talk.to_record())
    idea_ids = {a.idea_id for a in pending if a.idea_id}
    if idea_ids or any(a.kind in ("record_idea", "attribute") for a in pending):
        files[f"{ledger.RECORD_DIR}/ideas.json"] = _json(
            {"ideas": [i.to_record() for i in project.ideas.select_related("confirmed_by")]})
    if any(a.kind.startswith("method") for a in pending):
        files[f"{ledger.RECORD_DIR}/methods.json"] = _json(
            {"runs": [r.to_record() for r in project.method_runs.prefetch_related("steps")]})
    trailers = [("Sahalekhana", "v0")] + [("Act", a.trailer(i)) for i, a in enumerate(pending, 1)]
    compose = composition.trailer(project)
    if compose:
        trailers.append(("Compose", compose))
    name, email = _identity(user)
    sha = ledger.commit(ledger.repo_path(project.slug), files=files, message=message,
                        trailers=trailers, author_name=name, author_email=email)
    Act.objects.filter(pk__in=[a.pk for a in pending]).update(commit_sha=sha)
    composition.attach(project, sha)
    return sha


def _path(project: Project):
    return ledger.repo_path(project.slug)


def repo(project: Project):
    """The project's git repository path (callers take ``ledger.lock`` on it)."""
    return _path(project)


def commit_records(project: Project, user, *, message: str) -> str:
    """Commit the pending acts and their records without touching main.tex —
    what a discussion, an attribution or an export produces."""
    return _commit(project, user, files={}, message=message)


def source(project: Project) -> str:
    return ledger.read(_path(project), latex.MAIN)


def head(project: Project) -> str:
    return ledger.head(_path(project)) or ""


def _unit_id(value: str, *, required: bool) -> str:
    value = (value or "").strip()
    if not value and not required:
        return ""
    if not latex.UNIT_ID_RE.match(value):
        raise InvalidAction("Unit ids use letters, digits, dots, colons or hyphens, e.g. intro.thesis.")
    return value


# ── projects and access ──────────────────────────────────────────────────────

def create_project(*, user, title: str, slug: str) -> Project:
    path = ledger.repo_path(slug)
    if Project.objects.filter(slug=slug).exists() or path.exists():
        raise InvalidAction("A project with that short name already exists. Choose another.")
    try:
        with transaction.atomic():
            project = Project.objects.create(slug=slug, title=title, created_by=user)
            Membership.objects.create(project=project, user=user, role="author", granted_by=user)
            Act.objects.create(project=project, user=user, agent=human(user),
                               role="svatantra", kind="frame", note="Created the project")
            ledger.init(path)
            _commit(project, user, message=f"Create {title}",
                    files={latex.MAIN: latex.scaffold(title), latex.STY_NAME: latex.STY})
    except ledger.LedgerError:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return project


def set_tracking(*, project: Project, user, on: bool) -> Project:
    """Turn composition tracking on or off for this paper. Either way it is an
    act, so the record says when the paper stopped counting how text arrived."""
    if project.track_composition == on:
        return project
    project.track_composition = on
    project.save(update_fields=["track_composition"])
    with ledger.lock(_path(project)), transaction.atomic():
        Act.objects.create(project=project, user=user, agent=human(user), role="svatantra",
                           kind="set_tracking", note="on" if on else "off")
        commit_records(project, user,
                       message=f"Composition tracking {'on' if on else 'off'}")
    return project


def grant(*, project: Project, user, username: str, role: str) -> Membership:
    """Give ``username`` author or reviewer access. The grant is itself an act."""
    if role not in dict(grammar.MEMBER_ROLES):
        raise InvalidAction("Choose author or reviewer.")
    target = get_user_model().objects.filter(username=username.strip()).first()
    if target is None:
        raise InvalidAction(f"No account named {username.strip()!r}. Accounts are created by the site's coordinators.")
    with ledger.lock(_path(project)), transaction.atomic():
        membership, created = Membership.objects.get_or_create(
            project=project, user=target, defaults={"role": role, "granted_by": user})
        if not created:
            if membership.role == role:
                raise InvalidAction(f"{target.get_username()} is already a {role} on this paper.")
            if membership.role == "author" and project.memberships.filter(role="author").count() == 1:
                raise InvalidAction("A paper needs at least one author.")
            membership.role, membership.granted_by = role, user
            membership.save(update_fields=["role", "granted_by"])
        Act.objects.create(project=project, user=user, agent=human(user), role="svatantra", kind="grant",
                           note=f"{role}:{target.get_username()}")
        _commit(project, user, files={}, message=f"Grant {role} access to {target.get_username()}")
    return membership


# ── the writer's own edits ───────────────────────────────────────────────────

def review_changes(project: Project, content: str) -> list[latex.Change]:
    return latex.changes(source(project), latex.normalize(content))


def save(*, project: Project, user, content: str, base_sha: str, message: str,
         meaning: set[str]) -> str:
    """Commit the writer's edit. ``meaning`` holds the change keys the writer
    confirmed as meaning-bearing; every other change is recorded as a rephrase.
    Commits that did not touch main.tex (reviews, grants) do not block a save."""
    content = latex.normalize(content)
    latex.parse_units(content)
    latex.parse_owed(content)
    path = _path(project)
    with ledger.lock(path):
        if (ledger.head(path) or "") != base_sha and ledger.file_changed_since(path, base_sha, latex.MAIN):
            raise Conflict("Someone committed a change to the paper since you opened it. Copy your "
                           "edits, reload the page, and apply them again.")
        old = ledger.read(path, latex.MAIN)
        found = latex.changes(old, content)
        if not found:
            raise InvalidAction("Nothing changed since the last commit.")
        with transaction.atomic():
            for ch in found:
                bearing = ch.key in meaning
                Act.objects.create(
                    project=project, user=user, agent=human(user), role="svatantra",
                    kind="draft" if bearing else "rephrase", yukti=ch.yukti, unit_id=ch.unit_id,
                    meaning_bearing=bearing, note=ch.status)
            for text in latex.added_owed(old, content):
                Act.objects.create(project=project, user=user, agent=human(user), role="svatantra",
                                   kind="reserve", note=text)
            summary = ", ".join(ch.unit_id or "text outside units" for ch in found[:4])
            return _commit(project, user, files={latex.MAIN: content},
                           message=message.strip() or f"Edit {summary}")


# ── asking the AI ────────────────────────────────────────────────────────────

_CANDIDATE_KIND = {"purvapaksha": "object", "uttarapaksha": "rebut"}


def _consult(thread: Ask, device, *, user, answer_kind: str) -> None:
    """Run the AI for ``thread`` and record each answer as a candidate + act.
    A failure marks the thread failed; the instruction act stays recorded."""
    text = source(thread.project)
    try:
        units = {u.id: u for u in latex.parse_units(text)}
    except latex.LatexError:
        units = {}
    try:
        proposals, model = ai.propose(
            source=text, unit_id=thread.unit_id,
            unit_body=units[thread.unit_id].body if thread.unit_id in units else None,
            device=device, count=thread.count, instruction=thread.instruction,
            constraint=thread.constraint, purpose=thread.purpose)
    except ai.AIFailure as exc:
        thread.status, thread.error = Ask.Status.FAILED, str(exc)
        thread.save(update_fields=["status", "error"])
        return
    thread.model = model
    thread.save(update_fields=["model"])
    for i, p in enumerate(proposals, 1):
        cand = Candidate.objects.create(ask=thread, index=i, text=p["text"], check_text=p["check"])
        Act.objects.create(
            project=thread.project, ask=thread, candidate=cand, user=user, agent=f"ai:{model}",
            role="prayojya", kind=answer_kind, yukti=thread.yukti, unit_id=thread.unit_id,
            meaning_bearing=True if thread.purpose == Ask.Purpose.WRITE else None)


def ask(*, project: Project, user, unit_id: str, device: str, count: int,
        instruction: str, constraint: str) -> Ask:
    """An author asks for candidates to place in the paper. Nothing is committed
    until a candidate is accepted (or the next commit carries the asks)."""
    spec = grammar.DEVICE.get(device)
    if spec is None:
        raise InvalidAction("Choose what you are asking for.")
    unit_id = _unit_id(unit_id, required=True)
    thread = Ask.objects.create(
        project=project, purpose=Ask.Purpose.WRITE, unit_id=unit_id, device_code=spec.code,
        yukti=spec.yukti, count=max(1, min(int(count or 3), 5)), instruction=instruction.strip(),
        constraint=constraint.strip(), requested_by=user, model=settings.LEKHANA_MODEL)
    Act.objects.create(project=project, ask=thread, user=user, agent=human(user), role="prayojaka",
                       kind="instruct", yukti=spec.yukti, unit_id=unit_id,
                       note=" | ".join(filter(None, [thread.instruction, thread.constraint])))
    _consult(thread, spec, user=user, answer_kind=_CANDIDATE_KIND.get(spec.yukti, "draft"))
    return thread


def review_ask(*, project: Project, user, unit_id: str, device: str, instruction: str,
               count: int = 3) -> Ask:
    """A reviewer asks the paper. The answers never touch main.tex, and the whole
    exchange is committed at once so the review record is never pending."""
    spec = grammar.REVIEW_DEVICE.get(device)
    if spec is None:
        raise InvalidAction("Choose what you want the paper to produce.")
    unit_id = _unit_id(unit_id, required=False)
    thread = Ask.objects.create(
        project=project, purpose=Ask.Purpose.REVIEW, unit_id=unit_id, device_code=spec.code,
        yukti=spec.yukti, count=max(1, min(int(count or 3), 8)), instruction=instruction.strip(),
        requested_by=user, model=settings.LEKHANA_MODEL)
    Act.objects.create(project=project, ask=thread, user=user, agent=human(user),
                       role=_review_role(user, project), kind="review_ask", yukti=spec.yukti,
                       unit_id=unit_id, note=thread.instruction)
    _consult(thread, spec, user=user, answer_kind="explain")
    if thread.status != Ask.Status.FAILED:
        thread.status = Ask.Status.RESOLVED
        thread.save(update_fields=["status"])
    where = f" on {unit_id}" if unit_id else ""
    with ledger.lock(_path(project)):
        _commit(project, user, files={}, message=f"Review: {spec.label.lower()}{where}")
    return thread


def _proposed(candidate: Candidate) -> None:
    if candidate.ask.purpose != Ask.Purpose.WRITE:
        raise InvalidAction("Answers to a reviewer aren't placed in the paper.")
    if candidate.status != Candidate.Status.PROPOSED:
        raise InvalidAction(f"Candidate ({candidate.letter}) was already {candidate.status}.")


def _close_if_done(thread: Ask) -> None:
    if not thread.candidates.filter(status=Candidate.Status.PROPOSED).exists():
        thread.status = Ask.Status.RESOLVED
        thread.save(update_fields=["status"])


def examine(candidate: Candidate, user) -> None:
    """Open the candidate's check. Accepting after this is an examined acceptance."""
    _proposed(candidate)
    if candidate.examined_at is None:
        candidate.examined_at, candidate.examined_by = timezone.now(), user
        candidate.save(update_fields=["examined_at", "examined_by"])


def reject(candidate: Candidate, user, *, reason: str, note: str = "") -> None:
    """Recorded now, carried into git by the project's next commit."""
    _proposed(candidate)
    r = grammar.REASON.get(reason)
    if r is None:
        raise InvalidAction("Choose why you are rejecting this candidate.")
    if r.code == "other" and not note.strip():
        raise InvalidAction("Say why, when the reason is Other.")
    thread = candidate.ask
    with transaction.atomic():
        candidate.status, candidate.reason, candidate.reason_note = Candidate.Status.REJECTED, r.code, note.strip()
        candidate.decided_by, candidate.decided_at = user, timezone.now()
        candidate.save()
        Act.objects.create(project=thread.project, ask=thread, candidate=candidate, user=user,
                           agent=human(user), role="sakshin", kind="flag_defect" if r.defect else "reject",
                           yukti=thread.yukti, unit_id=thread.unit_id, defect=r.code, note=note.strip())
        _close_if_done(thread)


def _placement(src: str, thread: Ask, body: str) -> tuple[str, str]:
    """Where an accepted candidate goes. Same device on an existing unit: rewrite
    it. A different device: a new unit right after it. No such unit: at \\ask{id}
    or the end. Returns (new source, the unit id it now lives in)."""
    units = {u.id: u for u in latex.parse_units(src)}
    target = units.get(thread.unit_id)
    if target is None:
        return latex.insert_unit(src, thread.unit_id, thread.yukti, body), thread.unit_id
    if target.yukti == thread.yukti:
        return latex.replace_body(src, target, body), thread.unit_id
    uid, n = f"{thread.unit_id}.{thread.yukti}", 2
    while uid in units:
        uid, n = f"{thread.unit_id}.{thread.yukti}-{n}", n + 1
    return latex.insert_after(src, target, latex.unit_command(uid, thread.yukti, body)), uid


def accept(candidate: Candidate, user) -> str:
    """Place the candidate in main.tex and commit, with every pending act."""
    _proposed(candidate)
    thread = candidate.ask
    project = thread.project
    path = _path(project)
    body, owed_count = latex.render_candidate(candidate.text)
    with ledger.lock(path):
        new_source, placed = _placement(ledger.read(path, latex.MAIN), thread, body)
        try:
            latex.parse_units(new_source)
        except latex.LatexError as exc:
            raise InvalidAction(
                f"Placing this candidate would leave the paper unreadable ({exc}). If \\ask{{{thread.unit_id}}} "
                "sits inside another \\yukti{…}, move the marker outside it, commit, and accept again.") from exc
        with transaction.atomic():
            candidate.status, candidate.placed_unit = Candidate.Status.ACCEPTED, placed
            candidate.decided_by, candidate.decided_at = user, timezone.now()
            candidate.save()
            Act.objects.create(
                project=project, ask=thread, candidate=candidate, user=user, agent=human(user),
                role="sakshin", kind="affirm" if candidate.examined_at else "unexamined_assent",
                yukti=thread.yukti, unit_id=placed, meaning_bearing=True)
            for _ in range(owed_count):
                Act.objects.create(project=project, ask=thread, candidate=candidate, user=user,
                                   agent=f"ai:{thread.model}", role="prayojya", kind="reserve",
                                   yukti=thread.yukti, unit_id=placed)
            _close_if_done(thread)
            return _commit(project, user, files={latex.MAIN: new_source},
                           message=f"{placed}: accept {grammar.iast(thread.yukti)} candidate ({candidate.letter})")


# ── review points ────────────────────────────────────────────────────────────

def add_point(*, project: Project, user, unit_id: str, kind: str, text: str) -> ReviewPoint:
    if kind not in dict(grammar.REVIEW_POINT_KINDS):
        raise InvalidAction("Choose what kind of point this is.")
    if not text.strip():
        raise InvalidAction("Write the point before sending it.")
    unit_id = _unit_id(unit_id, required=False)
    with ledger.lock(_path(project)), transaction.atomic():
        point = ReviewPoint.objects.create(project=project, unit_id=unit_id, kind=kind,
                                           text=text.strip(), author=user)
        Act.objects.create(project=project, review_point=point, user=user, agent=human(user),
                           role=_review_role(user, project), kind="review_point", unit_id=unit_id, note=kind)
        _commit(project, user, files={}, message=f"Review point ({kind}) on {unit_id or 'the paper'}")
    return point


def respond(point: ReviewPoint, user, *, text: str) -> None:
    """An author's response to a review point. One response per point: the
    record is append-only, so a response is not edited once given."""
    if point.response:
        raise InvalidAction("This point already has a response.")
    if not text.strip():
        raise InvalidAction("Write the response before sending it.")
    project = point.project
    with ledger.lock(_path(project)), transaction.atomic():
        point.response, point.responded_by, point.responded_at = text.strip(), user, timezone.now()
        point.save(update_fields=["response", "responded_by", "responded_at"])
        Act.objects.create(project=project, review_point=point, user=user, agent=human(user),
                           role="svatantra", kind="respond", unit_id=point.unit_id)
        _commit(project, user, files={}, message=f"Respond to review point {point.pk}")


# ── reading the record ───────────────────────────────────────────────────────

def profile(project: Project) -> tuple[list[dict], int]:
    """Acts by family, human and AI, plus the count of unexamined acceptances
    (recorded, never acknowledged). A profile, never a percentage."""
    counts = {fam: {"family": label, "human": 0, "ai": 0} for fam, label in grammar.ACT_FAMILIES.items()}
    unexamined = 0
    for kind, agent in project.acts.values_list("kind", "agent"):
        fam = grammar.ACT_FAMILY.get(kind)
        if fam is None:
            unexamined += 1
        elif fam in counts:
            counts[fam]["ai" if agent.startswith("ai:") else "human"] += 1
    rows = list(counts.values())
    for row in rows:
        total = row["human"] + row["ai"]
        row["human_pct"] = round(100 * row["human"] / total) if total else 0
        row["ai_pct"] = 100 - row["human_pct"] if total else 0
    return rows, unexamined


def unit_origins(project: Project) -> dict[str, dict]:
    """How each unit's current text got there: written by a person, placed from
    an AI candidate after examining it, placed without examining, or unrecorded
    (e.g. the scaffold). ``edited`` = a form-only edit came after."""
    out: dict[str, dict] = {}
    rows = project.acts.filter(kind__in=["draft", "rephrase", "affirm", "unexamined_assent"]) \
        .exclude(unit_id="").order_by("id").values_list("unit_id", "kind", "agent")
    for uid, kind, agent in rows:
        info = out.setdefault(uid, {"origin": "unrecorded", "edited": False, "human": 0, "ai": 0})
        if kind == "draft" and not agent.startswith("ai:"):
            info.update(origin="human", edited=False)
        elif kind == "affirm":
            info.update(origin="ai_examined", edited=False)
        elif kind == "unexamined_assent":
            info.update(origin="ai_unexamined", edited=False)
        elif kind == "rephrase":
            info["edited"] = True
    for uid, agent in project.acts.exclude(unit_id="").values_list("unit_id", "agent"):
        if uid in out:
            out[uid]["ai" if agent.startswith("ai:") else "human"] += 1
    return out


def paper_blocks(text: str, origins: dict[str, dict]) -> list[dict]:
    """The paper as a reader sees it: sections, and paragraphs of units."""
    events = sorted([(s.start, "section", s) for s in latex.parse_sections(text)]
                    + [(u.start, "unit", u) for u in latex.parse_units(text)], key=lambda e: e[0])
    blocks: list[dict] = []
    para, prev_end = None, None
    for _, kind, item in events:
        if kind == "section":
            blocks.append({"type": "section", "title": latex.to_plain(item.title)})
            para, prev_end = None, None
            continue
        gap = text[prev_end:item.start] if prev_end is not None else ""
        if para is None or re.search(r"\n[ \t]*\n", gap):
            para = {"type": "para", "units": []}
            blocks.append(para)
        info = origins.get(item.id, {})
        y = grammar.YUKTI.get(item.yukti)
        para["units"].append({
            "id": item.id, "yukti": item.yukti, "iast": grammar.iast(item.yukti),
            "family": y.family if y else "other", "text": latex.to_plain(item.body),
            "origin": info.get("origin", "unrecorded"), "edited": info.get("edited", False),
            "human": info.get("human", 0), "ai": info.get("ai", 0)})
        prev_end = item.end
    return blocks


def unit_history(project: Project, uid: str) -> dict:
    """Every version of one unit across the git history (newest first), the acts
    committed with each version, every act on the unit, and the writing threads
    behind it."""
    acts = list(project.acts.filter(Q(unit_id=uid) | Q(candidate__placed_unit=uid))
                .select_related("candidate", "ask").order_by("id"))
    by_sha = defaultdict(list)
    for a in acts:
        if a.commit_sha:
            by_sha[a.commit_sha].append(a)
    versions, prev = [], None
    for entry in reversed(ledger.file_history(_path(project), latex.MAIN)):
        try:
            units = {u.id: u for u in latex.parse_units(entry["text"])}
        except latex.LatexError:
            continue
        body = units[uid].body if uid in units else None
        if body != prev:
            versions.append({"sha": entry["sha"], "author": entry["author"], "date": entry["date"],
                             "subject": entry["subject"],
                             "text": None if body is None else latex.to_plain(body),
                             "acts": by_sha.get(entry["sha"], [])})
        prev = body
    versions.reverse()
    threads = Ask.objects.filter(pk__in={a.ask_id for a in acts if a.ask_id}, purpose=Ask.Purpose.WRITE) \
        .select_related("requested_by").prefetch_related("candidates")
    return {"versions": versions, "acts": acts, "threads": threads,
            "pending": [a for a in acts if not a.commit_sha]}
