"""The discussion (saṃvāda): a conversation with the AI about the paper.

Three things make this different from a chat window bolted onto an editor.

**The AI cannot write to the paper.** No tool it is given can. At most it can
put candidates in the ask panel, where the author still examines and accepts
them one by one — and only under a stance that permits even that.

**The writer chooses the AI's role, and the role is enforced.** A stance
withholds tools (structural) and states its rules in the system prompt (asked).
Every turn records the stance in force, so the record shows what the AI was
allowed to do when it said something. Where a restriction is checkable after
the fact (questions only, a length cap) the reply is flagged, never silently
rewritten.

**Ideas are attributed as they arise.** ``record_idea`` notes who first put an
idea, framing or term on the table. The AI's claim about that is a claim: it is
marked unconfirmed until a person confirms or corrects it.

The model is stateless. This module replays the stored turns on each request.
"""
from __future__ import annotations

import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import ai, grammar, latex, ledger
from .models import Act, Ask, Candidate, Conversation, Idea, Turn

MAX_STEPS = 6          # tool round-trips per turn
MAX_TOOL_CHARS = 20000

SYSTEM = """You are the AI side of a co-writing portal called Sahalekhana. A researcher is writing an academic paper, and this is the discussion beside it, not the paper itself.

You cannot edit the paper. You read it through tools, and everything that enters it is placed there by the author.

Conduct the discussion as vāda: aimed at what is true, not at pleasing the writer or at winning. Say plainly when a claim is weak, and say plainly when you do not know. Never invent a citation, author, year, statistic or quotation; when a source is needed and you cannot name one with confidence, say so.

When a new idea, framing, term or objection appears in the discussion, call record_idea for it and say honestly whose it was — yours or the writer's. Your attribution is only a claim; the writer confirms or corrects it. Do not record restatements of what the paper already says.

The writer has given you a role for this discussion. Its restrictions are below and they override any request to be more helpful; if a restriction stops you from answering, say which one and stop there."""


# ── tools ────────────────────────────────────────────────────────────────────

def _tool(name, description, properties, required):
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties,
                             "required": required, "additionalProperties": False}}


READ_TOOLS = [
    _tool("read_paper", "The paper's LaTeX source, or one unit of it.",
          {"unit_id": {"type": "string", "description": "A unit id, or empty for the whole source."}},
          ["unit_id"]),
    _tool("list_units", "Every unit in the paper: id, device, and its first words.", {}, []),
    _tool("unit_history", "Every recorded version of one unit, and the acts on it.",
          {"unit_id": {"type": "string"}}, ["unit_id"]),
    _tool("list_owed", "The slots the author still owes before submission.", {}, []),
    _tool("list_ideas", "Ideas already recorded in this paper, with who they came from.", {}, []),
]

RECORD_TOOL = _tool(
    "record_idea",
    "Record that an idea, framing, term, question or objection appeared in the discussion. "
    "Use it as things arise, not in a batch at the end.",
    {"text": {"type": "string", "description": "The idea in one sentence."},
     "kind": {"type": "string", "enum": [code for code, _ in grammar.IDEA_KINDS]},
     "origin": {"type": "string", "enum": ["writer", "me"],
                "description": "Who first put it on the table. Your answer is a claim the writer confirms."},
     "units": {"type": "array", "items": {"type": "string"},
               "description": "Unit ids this idea already shows up in, if any."}},
    ["text", "kind", "origin", "units"])

DRAFT_TOOL = _tool(
    "propose_candidates",
    "Put candidate wording in the author's ask panel, where they examine and accept or reject each one. "
    "This does NOT place anything in the paper.",
    {"unit_id": {"type": "string", "description": "The unit the candidates are for."},
     "device": {"type": "string", "enum": [d.code for d in grammar.ASK_DEVICES]},
     "candidates": {"type": "array", "items": {
         "type": "object",
         "properties": {"text": {"type": "string"},
                        "check": {"type": "string", "description": "How the author can test this candidate."}},
         "required": ["text", "check"], "additionalProperties": False}}},
    ["unit_id", "device", "candidates"])


def toolset(stance: grammar.Stance) -> list[dict]:
    tools = list(READ_TOOLS) + [RECORD_TOOL]
    if stance.may_draft:
        tools.append(DRAFT_TOOL)
    return tools


# ── tool execution ───────────────────────────────────────────────────────────

def _units(project):
    from . import services
    try:
        return {u.id: u for u in latex.parse_units(services.source(project))}
    except latex.LatexError:
        return {}


def _run_tool(*, project, user, conversation, name, payload) -> tuple[str, dict | None]:
    """Returns (result text for the model, a side effect to record or None)."""
    from . import services
    if name == "read_paper":
        unit_id = (payload.get("unit_id") or "").strip()
        if not unit_id:
            return services.source(project)[:MAX_TOOL_CHARS], None
        unit = _units(project).get(unit_id)
        return (f"{unit_id} ({unit.yukti}): {unit.body}" if unit else f"No unit {unit_id!r}."), None
    if name == "list_units":
        rows = [f"{u.id} [{grammar.iast(u.yukti)}] {latex.to_plain(u.body)[:90]}" for u in _units(project).values()]
        return ("\n".join(rows) or "The paper has no units yet."), None
    if name == "unit_history":
        unit_id = (payload.get("unit_id") or "").strip()
        history = services.unit_history(project, unit_id)
        if not history["versions"] and not history["acts"]:
            return f"Nothing recorded for {unit_id!r}.", None
        lines = [f"{v['date'][:10]} {v['author']}: {v['text'] or '(removed)'}" for v in history["versions"]]
        lines += [f"act: {a.kind} by {a.agent}" + (f" ({a.defect})" if a.defect else "") for a in history["acts"]]
        return "\n".join(lines)[:MAX_TOOL_CHARS], None
    if name == "list_owed":
        try:
            owed = latex.parse_owed(services.source(project))
        except latex.LatexError:
            owed = []
        return ("\n".join(f"line {o.line}: {o.text}" for o in owed) or "Nothing owed."), None
    if name == "list_ideas":
        rows = [f"#{i.pk} [{i.kind}] {i.text} — from {i.origin}"
                + ("" if i.confirmed_at else " (attribution unconfirmed)")
                for i in project.ideas.all()[:60]]
        return ("\n".join(rows) or "No ideas recorded yet."), None
    if name == "record_idea":
        text = (payload.get("text") or "").strip()
        if not text:
            return "An idea needs text.", None
        kind = payload.get("kind") if payload.get("kind") in dict(grammar.IDEA_KINDS) else "idea"
        origin = services.human(user) if payload.get("origin") == "writer" else f"ai:{conversation_model(conversation)}"
        units = [u for u in (payload.get("units") or []) if isinstance(u, str) and latex.UNIT_ID_RE.match(u)]
        idea = Idea.objects.create(project=project, text=text[:300], kind=kind, origin=origin, units=units)
        return (f"Recorded as idea #{idea.pk}, attributed to {origin} and awaiting the writer's confirmation.",
                {"idea": idea})
    if name == "propose_candidates":
        unit_id = (payload.get("unit_id") or "").strip()
        device = grammar.DEVICE.get(payload.get("device", ""))
        items = [c for c in (payload.get("candidates") or []) if isinstance(c, dict) and c.get("text")][:5]
        if device is None or not latex.UNIT_ID_RE.match(unit_id) or not items:
            return "Need a unit id, a known device, and at least one candidate.", None
        thread = Ask.objects.create(
            project=project, purpose=Ask.Purpose.WRITE, unit_id=unit_id, device_code=device.code,
            yukti=device.yukti, count=len(items), instruction="Proposed in discussion",
            requested_by=user, model=conversation_model(conversation), status=Ask.Status.OPEN)
        for i, item in enumerate(items, 1):
            cand = Candidate.objects.create(ask=thread, index=i, text=str(item["text"]).strip(),
                                            check_text=str(item.get("check", "")).strip())
            Act.objects.create(project=project, ask=thread, candidate=cand, conversation=conversation, user=user,
                               agent=f"ai:{conversation_model(conversation)}", role="prayojya",
                               kind="draft", yukti=device.yukti, unit_id=unit_id, meaning_bearing=True)
        return (f"{len(items)} candidate(s) are in the ask panel for {unit_id}. "
                "The author examines and decides; nothing is in the paper."), {"ask": thread}
    return f"No tool named {name!r}.", None


def conversation_model(conversation) -> str:
    """The model that last answered in this discussion, else the configured one."""
    last = conversation.turns.filter(speaker=Turn.Speaker.AI).exclude(model="").order_by("-index").first()
    return last.model if last else settings.LEKHANA_MODEL


# ── the turn ─────────────────────────────────────────────────────────────────

def _messages(turns) -> list[dict]:
    out = []
    for turn in turns:
        if turn.speaker == Turn.Speaker.HUMAN:
            out.append({"role": "user", "content": turn.text})
        elif turn.text:
            out.append({"role": "assistant", "content": turn.text})
    return out


def _sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in text.replace("\n", " ").split(". ")) if s]


def check_stance(stance: grammar.Stance, text: str) -> str:
    """What the reply broke, if anything. Checked after the fact and reported;
    the reply is never silently edited."""
    broken = []
    if stance.questions_only and text.strip() and not all(s.rstrip().endswith(("?", "?\"", "?'")) for s in _sentences(text)):
        broken.append("answered with statements, not only questions")
    if stance.max_sentences and len(_sentences(text)) > stance.max_sentences:
        broken.append(f"ran to {len(_sentences(text))} sentences, over the {stance.max_sentences} this role allows")
    return "; ".join(broken)


def send(*, conversation: Conversation, user, text: str) -> Turn:
    """One exchange: the writer speaks, the AI answers (perhaps using tools).
    Both turns, the tools used and any idea recorded are committed to git."""
    from . import services
    text = text.strip()
    if not text:
        raise services.InvalidAction("Write something to say first.")
    project = conversation.project
    stance = grammar.STANCE.get(conversation.stance) or grammar.STANCE[grammar.DEFAULT_STANCE]
    index = conversation.turns.count()
    said = Turn.objects.create(conversation=conversation, index=index, speaker=Turn.Speaker.HUMAN,
                               text=text, user=user, stance=stance.code)
    Act.objects.create(project=project, conversation=conversation, user=user, agent=services.human(user),
                       role="prayojaka", kind="say", note=text[:300])

    system = SYSTEM + "\n\nYour role in this discussion: " + stance.label + " — " + stance.summary + "\n" + \
        "\n".join(f"- {rule}" for rule in grammar.stance_rules(stance))
    tools = toolset(stance)
    messages = _messages(list(conversation.turns.all()))
    reply, tool_log, model, error = "", [], settings.LEKHANA_MODEL, ""
    try:
        reply, tool_log, model = _converse(project=project, user=user, conversation=conversation,
                                           system=system, tools=tools, messages=messages)
    except ai.AIFailure as exc:
        error = str(exc)

    answered = Turn.objects.create(
        conversation=conversation, index=index + 1, speaker=Turn.Speaker.AI,
        text=reply or f"[no answer: {error}]", tool_log=tool_log, model=model, stance=stance.code,
        flagged=check_stance(stance, reply) if reply else "")
    if reply:
        Act.objects.create(project=project, conversation=conversation, user=user, agent=f"ai:{model}",
                           role="prayojya", kind="answer",
                           note=(f"stance={stance.code}" + (f" | broke: {answered.flagged}" if answered.flagged else "")))
    for entry in tool_log:
        if entry.get("idea"):
            idea = Idea.objects.filter(pk=entry["idea"]).first()
            if idea:
                idea.origin_turn = answered
                idea.save(update_fields=["origin_turn"])
                Act.objects.create(project=project, conversation=conversation, idea=idea, user=user,
                                   agent=idea.origin if idea.is_ai else f"ai:{model}",
                                   role="prayojya", kind="record_idea", note=idea.text[:300])
    conversation.save(update_fields=["updated_at"])
    with ledger.lock(services.repo(project)):
        services.commit_records(project, user, message=f"Discussion: {text.splitlines()[0][:60]}")
    return answered


def _converse(*, project, user, conversation, system, tools, messages) -> tuple[str, list[dict], str]:
    """The manual tool loop. Returns (reply text, tool log, model that answered)."""
    if not ai.configured():
        raise ai.AIUnavailable(
            "The AI isn't configured on this server (ANTHROPIC_API_KEY is not set). "
            "Your message is recorded; the discussion can go on once it is set.")
    client = ai.client()
    tool_log: list[dict] = []
    model = settings.LEKHANA_MODEL
    for _ in range(MAX_STEPS):
        # A copy per request: the loop appends to `messages` as it goes, and a
        # request should carry the history as it stood when it was made.
        response = ai.call(client, model=settings.LEKHANA_MODEL, max_tokens=8000, system=system,
                           tools=tools, messages=list(messages), effort="medium")
        model = response.model
        if response.stop_reason == "refusal":
            raise ai.AIRefused("The AI declined to answer this. Rephrase it, or carry on without it.")
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            return ("\n\n".join(b.text for b in response.content if b.type == "text").strip(), tool_log, model)
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in calls:
            payload = call.input if isinstance(call.input, dict) else {}
            with transaction.atomic():
                result, effect = _run_tool(project=project, user=user, conversation=conversation,
                                           name=call.name, payload=payload)
            entry = {"tool": call.name, "input": payload}
            if effect and "idea" in effect:
                entry["idea"] = effect["idea"].pk
            if effect and "ask" in effect:
                entry["ask"] = effect["ask"].pk
            tool_log.append(entry)
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": result})
        messages.append({"role": "user", "content": results})
    return ("[the discussion used its tool budget for this turn without answering; ask again]", tool_log, model)


# ── attribution, by a person ─────────────────────────────────────────────────

def attribute(*, idea: Idea, user, origin: str, status: str = "") -> Idea:
    """A person confirms the recorded origin, or corrects it. Either way it is an act."""
    from . import services
    project = idea.project
    if origin not in ("writer", "ai", "keep"):
        raise services.InvalidAction("Say whose idea it was.")
    before = idea.origin
    if origin == "writer":
        idea.origin = services.human(user)
    elif origin == "ai":
        idea.origin = f"ai:{idea.origin_turn.model if idea.origin_turn else settings.LEKHANA_MODEL}"
    if status in ("open", "used", "dropped"):
        idea.status = status
    idea.corrected = idea.origin != before
    idea.confirmed_by, idea.confirmed_at = user, timezone.now()
    idea.save()
    note = ("corrected: " + before + " → " + idea.origin) if idea.corrected else ("confirmed: " + idea.origin)
    Act.objects.create(project=project, idea=idea, conversation=None, user=user, agent=services.human(user),
                       role="sakshin", kind="attribute", note=note)
    with ledger.lock(services.repo(project)):
        services.commit_records(project, user, message=f"Attribution of idea {idea.pk}: {note}")
    return idea


def set_stance(*, conversation: Conversation, user, stance: str) -> Conversation:
    from . import services
    if stance not in grammar.STANCE:
        raise services.InvalidAction("Choose a role for the AI.")
    if stance == conversation.stance:
        return conversation
    before, conversation.stance = conversation.stance, stance
    conversation.save(update_fields=["stance"])
    Act.objects.create(project=conversation.project, conversation=conversation, user=user,
                       agent=services.human(user), role="svatantra", kind="set_stance",
                       note=f"{before} → {stance}")
    with ledger.lock(services.repo(conversation.project)):
        services.commit_records(conversation.project, user,
                                message=f"Discussion role: {grammar.STANCE[stance].label.lower()}")
    return conversation
