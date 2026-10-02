"""The AI co-writer: one model call per ask, returning typed candidates.

Every failure becomes an :class:`AIFailure` whose message is shown to the
writer as-is. The ask and its instruction are recorded either way.

The provider is OpenAI, reached through ``chat.completions``. Nothing outside
this module handles a provider-shaped object: :func:`call` returns a
:class:`Reply`, and tools are declared in the portal's own shape
(``name``/``description``/``input_schema``) and translated here. So the tool
loop in ``lekhana.chat`` is written against this module, not against an SDK.

What the move off Anthropic gave up, recorded here rather than discovered
later: there is no server-side fallback model any more, so a decline reaches
the writer as a refusal instead of being retried quietly on another model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import openai
from django.conf import settings

SYSTEM = """You are the AI co-writer on Sahalekhana, a portal where a researcher and an AI write an academic paper together and every act is recorded.

Someone working on the paper, its author or a reviewer, has asked you for one kind of move, usually named by a device from tantrayukti (the classical Indian grammar of composing a treatise). You return candidates. Nothing you return enters the paper until the author examines it, so write each one to be examined: make what it depends on visible, and use `check` to hand the person what they need to test it, including where it could fail.

Conduct the exchange as vāda: a discussion aimed at what is true, not at pleasing the author or at winning. If the paragraph's claim is weak, a candidate may say so. State objections at their full strength.

Never invent a citation, author, year, statistic, quotation or dataset. When a candidate needs a source you cannot name with confidence, put a marker in its text: [[owed: what source is needed]]. The portal turns the marker into a slot the author must fill before submission.

Write candidate text as plain prose that can go straight into the paper, in the paper's own register. Do not use LaTeX commands or Markdown. Make the candidates genuinely different from one another."""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "check": {"type": "string"}},
                "required": ["text", "check"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


class AIFailure(Exception):
    """The ask produced no candidates. ``str(exc)`` is written for the writer."""


class AIUnavailable(AIFailure):
    pass


class AIRefused(AIFailure):
    pass


# ── what a turn looks like to the rest of the portal ─────────────────────────

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class Reply:
    """One model turn in the portal's terms. ``message`` is that same turn in
    the form the next request has to replay it back in."""
    text: str = ""
    model: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    message: dict = field(default_factory=dict)
    refusal: str = ""
    truncated: bool = False


def configured() -> bool:
    return bool(settings.OPENAI_API_KEY)


def _client() -> openai.OpenAI:
    return openai.OpenAI(api_key=settings.OPENAI_API_KEY, timeout=150.0, max_retries=1)


def client() -> openai.OpenAI:
    """The client, for callers that drive their own loop (see ``lekhana.chat``)."""
    if not configured():
        raise AIUnavailable(
            "The AI isn't configured on this server (OPENAI_API_KEY is not set).")
    return _client()


# ── the request ──────────────────────────────────────────────────────────────

def _function(tool: dict) -> dict:
    """A portal tool declaration as an OpenAI function tool."""
    return {"type": "function",
            "function": {"name": tool["name"], "description": tool.get("description", ""),
                         "parameters": tool["input_schema"], "strict": tool.get("strict", True)}}


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _create(client, **kwargs):
    """One request, with the SDK's errors mapped to AIFailure."""
    try:
        return client.chat.completions.create(**kwargs)
    except openai.BadRequestError as exc:
        # A model with no reasoning budget rejects the knob rather than
        # ignoring it. The ask is still worth making without it.
        if "reasoning_effort" in kwargs and "reasoning_effort" in str(exc):
            kwargs.pop("reasoning_effort")
            return _create(client, **kwargs)
        raise AIFailure(f"The AI service returned an error ({exc.status_code}). Try again later.") from exc
    except openai.RateLimitError as exc:
        raise AIFailure("The AI service is rate-limited right now. Try again in a minute.") from exc
    except openai.APIStatusError as exc:
        raise AIFailure(f"The AI service returned an error ({exc.status_code}). Try again later.") from exc
    except openai.APIConnectionError as exc:
        raise AIFailure("Couldn't reach the AI service. Check the server's network, then try again.") from exc


def _reply(response) -> Reply:
    choice = response.choices[0]
    message = choice.message
    calls, replay = [], []
    for item in (message.tool_calls or []):
        function = getattr(item, "function", None)
        if function is None:      # a non-function tool call; the portal declares none
            continue
        try:
            payload = json.loads(function.arguments or "{}")
        except ValueError:
            payload = {}
        calls.append(ToolCall(id=item.id, name=function.name,
                              input=payload if isinstance(payload, dict) else {}))
        replay.append({"id": item.id, "type": "function",
                       "function": {"name": function.name, "arguments": function.arguments or "{}"}})
    turn = {"role": "assistant", "content": message.content or ""}
    if replay:
        turn["tool_calls"] = replay
    refusal = (message.refusal or "").strip()
    if not refusal and choice.finish_reason == "content_filter":
        refusal = "content filtered"
    return Reply(text=(message.content or "").strip(), model=response.model, tool_calls=tuple(calls),
                 message=turn, refusal=refusal, truncated=choice.finish_reason == "length")


def call(client, *, model, max_tokens, system, messages, tools=None, schema=None, effort="high") -> Reply:
    """One request, normalised to a :class:`Reply`. Effort is ``high`` for
    one-shot asks and ``medium`` for discussion turns, which run several tool
    round-trips inside one web request and would otherwise outlast the worker's
    timeout. Reasoning tokens come out of ``max_tokens``, so the budget passed
    here is well above the length of the answer it has to leave room for."""
    kwargs = dict(model=model, max_completion_tokens=max_tokens, reasoning_effort=effort,
                  messages=[{"role": "system", "content": system}] + list(messages))
    if tools:
        kwargs["tools"] = [_function(t) for t in tools]
    if schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "candidates", "strict": True, "schema": schema}}
    return _reply(_create(client, **kwargs))


def tool_result(call: ToolCall, content: str) -> dict:
    """What one tool answered, in the form the next request replays it in."""
    return {"role": "tool", "tool_call_id": call.id, "content": content}


# ── the ask ──────────────────────────────────────────────────────────────────

def build_prompt(*, source: str, unit_id: str, unit_body: str | None, device, count: int,
                 instruction: str, constraint: str, purpose: str = "write") -> str:
    if purpose == "review":
        audience = ("You are answering a reviewer of this paper, not its author. Nothing you return is "
                    "placed in the paper. Refer to parts of the paper by their unit ids (the first "
                    "argument of \\yukti).")
    else:
        audience = "You are answering the paper's author. An accepted candidate is placed in the paper."
    if not unit_id:
        where = "The request is about the whole paper."
    elif unit_body is None:
        where = (f"Unit {unit_id} does not exist yet. An accepted candidate will be placed "
                 f"at \\ask{{{unit_id}}} if the paper has that marker, otherwise at the end.")
    else:
        where = f"Current text of unit {unit_id}:\n{unit_body}"
    name = f"{device.iast} ({device.label.lower()})" if device.iast else device.label
    return (
        f"<paper>\n{source}\n</paper>\n\n"
        f"{audience}\n\n{where}\n\n"
        f"Requested: {name}. {device.ask}\n"
        f"Request: {instruction.strip() or '(no further detail)'}\n"
        f"Constraint: {constraint.strip() or '(none)'}\n\n"
        f"Return {count} candidate(s). In each `check`: {device.check}"
    )


def propose(*, source: str, unit_id: str, unit_body: str | None, device, count: int,
            instruction: str, constraint: str, purpose: str = "write") -> tuple[list[dict], str]:
    """Returns (candidates as ``{"text", "check"}`` dicts, the model that answered)."""
    if not configured():
        raise AIUnavailable(
            "The AI co-writer isn't configured on this server (OPENAI_API_KEY is not set). "
            "Your request is recorded; write this part yourself or ask again once it is set.")
    prompt = build_prompt(source=source, unit_id=unit_id, unit_body=unit_body, device=device,
                          count=count, instruction=instruction, constraint=constraint, purpose=purpose)
    reply = call(_client(), model=settings.LEKHANA_MODEL, max_tokens=24000, system=SYSTEM,
                 messages=[{"role": "user", "content": prompt}], schema=SCHEMA, effort="high")

    if reply.refusal:
        raise AIRefused(f"The AI declined this request ({_short(reply.refusal)}). "
                        "Rephrase it, or write this part yourself.")
    if reply.truncated:
        raise AIFailure("The AI's answer was cut off. Ask for fewer candidates.")

    try:
        items = json.loads(reply.text)["candidates"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AIFailure("The AI's answer couldn't be read. Try again.") from exc
    proposals = [
        {"text": str(item.get("text", "")).strip(), "check": str(item.get("check", "")).strip()}
        for item in items if isinstance(item, dict) and str(item.get("text", "")).strip()
    ][:count]
    if not proposals:
        raise AIFailure("The AI returned no usable candidates. Try again with more detail.")
    return proposals, reply.model
