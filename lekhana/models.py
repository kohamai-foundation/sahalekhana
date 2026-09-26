"""Sahalekhana portal models.

The git repository is the record (see ``lekhana.ledger``). These tables are its
working state and query index: projects and who may do what on them, the asks
and their candidates, review points, and one ``Act`` row per recorded act. An
``Act`` with a blank ``commit_sha`` is pending — performed, not yet in git; the
next commit on the project carries it as a trailer.
"""
from django.conf import settings
from django.db import models

from . import grammar


class Project(models.Model):
    slug = models.SlugField(max_length=50, unique=True)
    title = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Membership", through_fields=("project", "user"),
        related_name="lekhana_projects")
    track_composition = models.BooleanField(
        default=True,
        help_text="Record how text arrives in the editor: characters typed, and each paste's "
                  "length and hash (never its text). Authors can turn it off; the change is an act.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Membership(models.Model):
    """An adhikāra on one paper: authors write; reviewers read, ask and comment."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="lekhana_memberships")
    role = models.CharField(max_length=16, choices=grammar.MEMBER_ROLES)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="lekhana_membership_unique")]
        ordering = ["role", "created_at"]


class Ask(models.Model):
    """One request to the AI for one device, and the thread (vimarśa) that grows
    around it. ``purpose`` = write: candidates may be placed in the paper.
    ``purpose`` = review: answers are recorded for the reviewer, never placed."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        FAILED = "failed", "Failed"

    class Purpose(models.TextChoices):
        WRITE = "write", "Writing"
        REVIEW = "review", "Reviewing"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="asks")
    purpose = models.CharField(max_length=8, choices=Purpose.choices, default=Purpose.WRITE)
    unit_id = models.CharField(max_length=64, blank=True, help_text="Blank = the whole paper.")
    device_code = models.CharField(max_length=32)
    yukti = models.CharField(max_length=32, blank=True)
    count = models.PositiveSmallIntegerField(default=3)
    instruction = models.TextField(blank=True)
    constraint = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    model = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def device(self):
        table = grammar.REVIEW_DEVICE if self.purpose == self.Purpose.REVIEW else grammar.DEVICE
        return table.get(self.device_code)

    def to_record(self) -> dict:
        return {
            "ask": self.pk, "purpose": self.purpose, "unit": self.unit_id,
            "device": self.device_code, "yukti": self.yukti, "count": self.count,
            "instruction": self.instruction, "constraint": self.constraint,
            "requested_by": self.requested_by.get_username() if self.requested_by else "",
            "model": self.model, "status": self.status, "error": self.error,
            "created_at": self.created_at.isoformat(),
            "candidates": [c.to_record() for c in self.candidates.all()],
        }


def _who(user) -> str:
    return user.get_username() if user else ""


def _when(moment) -> str:
    return moment.isoformat() if moment else ""


class Candidate(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    ask = models.ForeignKey(Ask, on_delete=models.CASCADE, related_name="candidates")
    index = models.PositiveSmallIntegerField()
    text = models.TextField()
    check_text = models.TextField(blank=True, help_text="The AI's account of how to test this candidate.")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    reason = models.CharField(max_length=40, blank=True)
    reason_note = models.TextField(blank=True)
    placed_unit = models.CharField(max_length=64, blank=True)
    examined_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    examined_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["index"]
        constraints = [models.UniqueConstraint(fields=["ask", "index"], name="lekhana_candidate_unique_index")]

    @property
    def letter(self) -> str:
        return "abcdefghij"[self.index - 1] if 1 <= self.index <= 10 else str(self.index)

    @property
    def reason_label(self) -> str:
        r = grammar.REASON.get(self.reason)
        return r.label if r else self.reason

    def to_record(self) -> dict:
        return {
            "index": self.index, "text": self.text, "check": self.check_text,
            "status": self.status, "reason": self.reason, "reason_note": self.reason_note,
            "placed_unit": self.placed_unit,
            "examined_by": _who(self.examined_by), "examined_at": _when(self.examined_at),
            "decided_by": _who(self.decided_by), "decided_at": _when(self.decided_at),
        }


class ReviewPoint(models.Model):
    """A reviewer's point on one unit (or the whole paper), and the author's response."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="review_points")
    unit_id = models.CharField(max_length=64, blank=True, help_text="Blank = the whole paper.")
    kind = models.CharField(max_length=16, choices=grammar.REVIEW_POINT_KINDS)
    text = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    response = models.TextField(blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def to_record(self) -> dict:
        return {
            "point": self.pk, "unit": self.unit_id, "kind": self.kind, "text": self.text,
            "author": _who(self.author), "created_at": _when(self.created_at),
            "response": self.response, "responded_by": _who(self.responded_by),
            "responded_at": _when(self.responded_at),
        }


class Conversation(models.Model):
    """A discussion (saṃvāda) about the paper between a writer and the AI.

    The AI is stateless: this is where the discussion lives, and it is replayed
    to the model on each turn. ``stance`` is the role the writer has given it,
    and it is recorded on every turn so the record shows the restrictions the
    AI was under when it said something."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=120, blank=True)
    stance = models.CharField(max_length=24, choices=[(s.code, s.label) for s in grammar.STANCES],
                              default=grammar.DEFAULT_STANCE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def to_record(self) -> dict:
        return {"conversation": self.pk, "title": self.title, "stance": self.stance,
                "created_by": _who(self.created_by), "created_at": _when(self.created_at),
                "turns": [t.to_record() for t in self.turns.all()]}


class Turn(models.Model):
    class Speaker(models.TextChoices):
        HUMAN = "human", "Writer"
        AI = "ai", "AI"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="turns")
    index = models.PositiveIntegerField()
    speaker = models.CharField(max_length=8, choices=Speaker.choices)
    text = models.TextField(blank=True)
    tool_log = models.JSONField(default=list, blank=True,
                                help_text="Tools the AI used on this turn, with their inputs.")
    model = models.CharField(max_length=64, blank=True)
    stance = models.CharField(max_length=24, blank=True)
    flagged = models.CharField(max_length=200, blank=True,
                               help_text="Set when the reply broke the stance's restrictions.")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["index"]
        constraints = [models.UniqueConstraint(fields=["conversation", "index"], name="lekhana_turn_unique_index")]

    def to_record(self) -> dict:
        return {"index": self.index, "speaker": self.speaker, "text": self.text,
                "tools": self.tool_log, "model": self.model, "stance": self.stance,
                "flagged": self.flagged, "user": _who(self.user), "at": _when(self.created_at)}


class Idea(models.Model):
    """Who first put an idea, a framing or a term on the table.

    ``origin`` is a claim until a person confirms it: the AI's account of who
    thought of something is not evidence. Confirming or correcting it is an act."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ideas")
    text = models.CharField(max_length=300)
    kind = models.CharField(max_length=16, choices=grammar.IDEA_KINDS, default="idea")
    origin = models.CharField(max_length=96, help_text="human:<username> or ai:<model>")
    origin_turn = models.ForeignKey(Turn, null=True, blank=True, on_delete=models.SET_NULL, related_name="ideas")
    units = models.JSONField(default=list, blank=True, help_text="Unit ids that realise this idea.")
    status = models.CharField(max_length=12, default="open",
                              choices=[("open", "Open"), ("used", "In the paper"), ("dropped", "Dropped")])
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    corrected = models.BooleanField(default=False, help_text="True when a person changed the claimed origin.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def is_ai(self) -> bool:
        return self.origin.startswith("ai:")

    def to_record(self) -> dict:
        return {"idea": self.pk, "text": self.text, "kind": self.kind, "origin": self.origin,
                "turn": self.origin_turn_id, "units": self.units, "status": self.status,
                "confirmed_by": _who(self.confirmed_by), "confirmed_at": _when(self.confirmed_at),
                "corrected": self.corrected, "created_at": _when(self.created_at)}


class MethodRun(models.Model):
    """A way of working the writer has chosen, e.g. expanding each sentence of
    the abstract. The portal tracks the steps; the writer does the writing."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="method_runs")
    code = models.CharField(max_length=32)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def to_record(self) -> dict:
        return {"method": self.code, "run": self.pk, "note": self.note,
                "created_by": _who(self.created_by), "created_at": _when(self.created_at),
                "steps": [s.to_record() for s in self.steps.all()]}


class MethodStep(models.Model):
    run = models.ForeignKey(MethodRun, on_delete=models.CASCADE, related_name="steps")
    index = models.PositiveIntegerField()
    label = models.CharField(max_length=300)
    unit_id = models.CharField(max_length=64, blank=True)
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["index"]
        constraints = [models.UniqueConstraint(fields=["run", "index"], name="lekhana_methodstep_unique_index")]

    def to_record(self) -> dict:
        return {"index": self.index, "label": self.label, "unit": self.unit_id,
                "done": self.done, "done_at": _when(self.done_at)}


class CompositionEvent(models.Model):
    """How text arrived in the editor, without keeping the text.

    A paste stores its length and a SHA-256 of its normalised content — enough
    to recognise it as something the portal's own AI proposed, or as text from
    elsewhere in this paper, and not enough to reconstruct anything. Typing is
    a running count. What a reviewer gets is the proportion, per commit and per
    unit, not a recording of the writer at work."""

    class Kind(models.TextChoices):
        TYPING = "typing", "Typed"
        PASTE = "paste", "Pasted"

    class Source(models.TextChoices):
        AI_CANDIDATE = "ai_candidate", "From the AI's candidates"
        OWN_PAPER = "own_paper", "From elsewhere in this paper"
        OUTSIDE = "outside", "From outside the portal"
        UNKNOWN = "unknown", "Unknown"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="composition")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    unit_id = models.CharField(max_length=64, blank=True)
    chars = models.PositiveIntegerField(default=0)
    text_hash = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, blank=True)
    matched_candidate = models.ForeignKey(
        Candidate, null=True, blank=True, on_delete=models.SET_NULL, related_name="pastes")
    commit_sha = models.CharField(max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class Snapshot(models.Model):
    """The editor buffer, saved every few seconds so work survives a crash.
    Kept in the database, never committed, and pruned to the last few."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="snapshots")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    content = models.TextField()
    chars = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Act(models.Model):
    """One recorded act, human or AI. Append-only: never edited except to stamp
    the commit that carries it into the git record."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="acts")
    conversation = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="acts")
    idea = models.ForeignKey(Idea, null=True, blank=True, on_delete=models.SET_NULL, related_name="acts")
    ask = models.ForeignKey(Ask, null=True, blank=True, on_delete=models.SET_NULL, related_name="acts")
    candidate = models.ForeignKey(
        Candidate, null=True, blank=True, on_delete=models.SET_NULL, related_name="acts")
    review_point = models.ForeignKey(
        ReviewPoint, null=True, blank=True, on_delete=models.SET_NULL, related_name="acts")
    agent = models.CharField(max_length=96, help_text="human:<username> or ai:<model>")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="The signed-in person this act was performed by or on behalf of.")
    role = models.CharField(max_length=16, choices=grammar.ROLES)
    kind = models.CharField(max_length=24, choices=[(c, label) for c, label, _ in grammar.ACT_KINDS])
    yukti = models.CharField(max_length=32, blank=True)
    unit_id = models.CharField(max_length=64, blank=True)
    meaning_bearing = models.BooleanField(null=True, blank=True)
    defect = models.CharField(max_length=40, blank=True)
    note = models.TextField(blank=True)
    commit_sha = models.CharField(max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    @property
    def is_ai(self) -> bool:
        return self.agent.startswith("ai:")

    def trailer(self, seq: int) -> str:
        parts = [str(seq), self.kind, f"agent={self.agent}", f"role={self.role}"]
        if self.yukti:
            parts.append(f"yukti={self.yukti}")
        if self.unit_id:
            parts.append(f"unit={self.unit_id}")
        if self.meaning_bearing is not None:
            parts.append("meaning=" + ("yes" if self.meaning_bearing else "no"))
        if self.defect:
            parts.append(f"defect={self.defect}")
        if self.ask_id:
            parts.append(f"vimarsha={self.ask_id}")
        if self.candidate_id:
            parts.append(f"candidate={self.candidate.letter}")
        if self.review_point_id:
            parts.append(f"point={self.review_point_id}")
        if self.conversation_id:
            parts.append(f"samvada={self.conversation_id}")
        if self.idea_id:
            parts.append(f"idea={self.idea_id}")
        return " ".join(parts)


class Anchor(models.Model):
    """A root hash over every project's HEAD, taken at one moment."""

    root_hash = models.CharField(max_length=64)
    heads = models.JSONField(default=list)
    log = models.CharField(max_length=24, default="local", help_text="local | opentimestamps")
    proof = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
