"""The AI co-writer: one Claude call per ask, returning typed candidates.

Every failure becomes an :class:`AIFailure` whose message is shown to the
writer as-is. The ask and its instruction are recorded either way.
"""
from __future__ import annotations

import json

import anthropic
from django.conf import settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"

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


def configured() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=150.0, max_retries=1)


def client() -> anthropic.Anthropic:
    """The client, for callers that drive their own loop (see ``lekhana.chat``)."""
    if not configured():
        raise AIUnavailable(
            "The AI isn't configured on this server (ANTHROPIC_API_KEY is not set).")
    return _client()


def call(client, *, model, max_tokens, system, messages, tools=None, effort="high"):
    """One request, with the errors mapped to AIFailure. Server-side fallbacks are
    on: a safety decline is retried on Anthropic's recommended model rather than
    surfacing as a refusal. Effort is ``high`` for one-shot asks and ``medium``
    for discussion turns, which run several tool round-trips inside one web
    request and would otherwise outlast the worker's timeout."""
    kwargs = dict(model=model, max_tokens=max_tokens, betas=[FALLBACK_BETA], fallbacks="default",
                  thinking={"type": "adaptive"}, output_config={"effort": effort},
                  system=system, messages=messages)
    if tools:
        kwargs["tools"] = tools
    try:
        return client.beta.messages.create(**kwargs)
    except anthropic.RateLimitError as exc:
        raise AIFailure("The AI service is rate-limited right now. Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise AIFailure(f"The AI service returned an error ({exc.status_code}). Try again later.") from exc
    except anthropic.APIConnectionError as exc:
        raise AIFailure("Couldn't reach the AI service. Check the server's network, then try again.") from exc


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
            "The AI co-writer isn't configured on this server (ANTHROPIC_API_KEY is not set). "
            "Your request is recorded; write this part yourself or ask again once it is set.")
    prompt = build_prompt(source=source, unit_id=unit_id, unit_body=unit_body, device=device,
                          count=count, instruction=instruction, constraint=constraint, purpose=purpose)
    try:
        response = _client().beta.messages.create(
            model=settings.LEKHANA_MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError as exc:
        raise AIFailure("The AI service is rate-limited right now. Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise AIFailure(f"The AI service returned an error ({exc.status_code}). Try again later.") from exc
    except anthropic.APIConnectionError as exc:
        raise AIFailure("Couldn't reach the AI service. Check the server's network, then try again.") from exc

    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None) if response.stop_details else None
        detail = f" ({category})" if category else ""
        raise AIRefused(f"The AI declined this request{detail}. Rephrase it, or write this part yourself.")
    if response.stop_reason == "max_tokens":
        raise AIFailure("The AI's answer was cut off. Ask for fewer candidates.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        items = json.loads(text)["candidates"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AIFailure("The AI's answer couldn't be read. Try again.") from exc
    proposals = [
        {"text": str(item.get("text", "")).strip(), "check": str(item.get("check", "")).strip()}
        for item in items if isinstance(item, dict) and str(item.get("text", "")).strip()
    ][:count]
    if not proposals:
        raise AIFailure("The AI returned no usable candidates. Try again with more detail.")
    return proposals, response.model
