"""Reading and editing the portal's LaTeX markup.

Three commands carry the record inside ordinary LaTeX (``sahalekhana.sty``
typesets them as plain text, so the source stays portable):

* ``\\adhikarana{label}{title}`` — a section.
* ``\\yukti{id}{device}{text}`` — an attributable unit, typed by its device.
* ``\\owed{what}`` — a reserved slot the author still owes before submission.
* ``\\ask{id}`` — where an accepted candidate for a new unit ``id`` is placed.

``\\yukti`` rather than ``\\unit``: siunitx (common in real papers) already
defines ``\\unit``.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

MAIN = "main.tex"
STY_NAME = "sahalekhana.sty"
STY = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{sahalekhana}[2026/09/14 v0 Sahalekhana markup]
% \adhikarana{label}{title} -- a section
\newcommand{\adhikarana}[2]{\section{#2}\label{#1}}
% \yukti{id}{device}{text} -- an attributable unit; typesets as its text
\newcommand{\yukti}[3]{#3}
% \owed{what} -- a reserved slot the author still owes
\newcommand{\owed}[1]{\fbox{\footnotesize owed: #1}}
% \ask{id} -- placement marker for a new unit; typesets as nothing
\newcommand{\ask}[1]{}
\endinput
"""

UNIT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class LatexError(ValueError):
    """The source can't be read as portal markup. The message names the line."""


@dataclass(frozen=True)
class Unit:
    id: str
    yukti: str
    body: str
    start: int
    end: int
    body_start: int
    body_end: int
    line: int


@dataclass(frozen=True)
class Owed:
    text: str
    start: int
    end: int
    line: int


@dataclass(frozen=True)
class Section:
    label: str
    title: str
    line: int
    start: int


@dataclass(frozen=True)
class Change:
    key: str          # "unit:<id>" or "outside"
    unit_id: str
    yukti: str
    status: str       # added | removed | changed | outside
    old: str
    new: str
    suggested_meaning: bool


def normalize(src: str) -> str:
    """Browsers post CRLF; the repository stores LF with a final newline."""
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    return src if src.endswith("\n") else src + "\n"


def _line(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _in_comment(src: str, pos: int) -> bool:
    start = src.rfind("\n", 0, pos) + 1
    i = start
    while i < pos:
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == "%":
            return True
        i += 1
    return False


def _read_group(src: str, i: int, what: str) -> tuple[str, int]:
    """``src[i]`` must open a brace group. Returns (content, index after it)."""
    if i >= len(src) or src[i] != "{":
        raise LatexError(f"line {_line(src, min(i, len(src)))}: {what} is missing an argument")
    depth, j = 0, i
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j], j + 1
        j += 1
    raise LatexError(f"line {_line(src, i)}: {what} has an unclosed brace")


def _skip_ws(src: str, i: int) -> int:
    while i < len(src) and src[i] in " \t\n":
        i += 1
    return i


def _commands(src: str, name: str):
    for m in re.finditer(r"\\" + name + r"(?![A-Za-z])\s*(?=\{)", src):
        if not _in_comment(src, m.start()):
            yield m


def parse_units(src: str) -> list[Unit]:
    units: list[Unit] = []
    seen: set[str] = set()
    last_end = -1
    for m in _commands(src, "yukti"):
        if m.start() < last_end:
            raise LatexError(f"line {_line(src, m.start())}: a \\yukti unit can't sit inside another")
        uid, i = _read_group(src, m.end(), "\\yukti")
        yk, i = _read_group(src, _skip_ws(src, i), "\\yukti")
        i = _skip_ws(src, i)
        body, end = _read_group(src, i, "\\yukti")
        uid, yk = uid.strip(), yk.strip()
        line = _line(src, m.start())
        if not UNIT_ID_RE.match(uid):
            raise LatexError(f"line {line}: unit id {uid!r} must use letters, digits, dots, colons or hyphens")
        if uid in seen:
            raise LatexError(f"line {line}: unit id {uid!r} is used twice")
        seen.add(uid)
        units.append(Unit(uid, yk, body, m.start(), end, i + 1, end - 1, line))
        last_end = end
    return units


def parse_owed(src: str) -> list[Owed]:
    out = []
    for m in _commands(src, "owed"):
        text, end = _read_group(src, m.end(), "\\owed")
        out.append(Owed(text.strip(), m.start(), end, _line(src, m.start())))
    return out


def parse_sections(src: str) -> list[Section]:
    out = []
    for m in _commands(src, "adhikarana"):
        label, i = _read_group(src, m.end(), "\\adhikarana")
        title, _ = _read_group(src, _skip_ws(src, i), "\\adhikarana")
        out.append(Section(label.strip(), title.strip(), _line(src, m.start()), m.start()))
    return out


def replace_body(src: str, unit: Unit, body: str) -> str:
    return src[:unit.body_start] + body + src[unit.body_end:]


def unit_command(uid: str, yukti: str, body: str) -> str:
    return "\\yukti{" + uid + "}{" + yukti + "}{" + body + "}"


def insert_after(src: str, unit: Unit, text: str) -> str:
    """Place ``text`` right after ``unit``, in the same paragraph."""
    return src[:unit.end] + " " + text + src[unit.end:]


def insert_unit(src: str, uid: str, yukti: str, body: str) -> str:
    """A new unit goes where ``\\ask{uid}`` marks, else before ``\\end{document}``."""
    text = unit_command(uid, yukti, body)
    for m in _commands(src, "ask"):
        marker, end = _read_group(src, m.end(), "\\ask")
        if marker.strip() == uid:
            return src[:m.start()] + text + src[end:]
    end_doc = src.find("\\end{document}")
    if end_doc != -1:
        return src[:end_doc] + text + "\n\n" + src[end_doc:]
    return src.rstrip("\n") + "\n" + text + "\n"


_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_OWED_MARK = re.compile(r"\[\[\s*owed\s*:\s*([^\]\n]+?)\s*\]\]", re.IGNORECASE)


def latex_escape(text: str) -> str:
    return "".join(_ESCAPES.get(c, c) for c in text)


def render_candidate(text: str) -> tuple[str, int]:
    """Plain prose from the AI -> LaTeX. ``[[owed: …]]`` markers become ``\\owed{…}``.
    Returns (latex, number of owed slots created)."""
    out, pos, count = [], 0, 0
    for m in _OWED_MARK.finditer(text):
        out.append(latex_escape(text[pos:m.start()]))
        out.append("\\owed{" + latex_escape(m.group(1)) + "}")
        count += 1
        pos = m.end()
    out.append(latex_escape(text[pos:]))
    return " ".join("".join(out).split()), count


def scaffold(title: str) -> str:
    return (
        "\\documentclass{article}\n"
        "\\usepackage{sahalekhana}\n\n"
        "\\title{" + latex_escape(title) + "}\n"
        "\\begin{document}\n"
        "\\maketitle\n\n"
        "\\adhikarana{sec:intro}{Introduction}\n"
        "\\yukti{intro.thesis}{uddesha}{State the paper's thesis in one sentence.}\n\n"
        "\\end{document}\n"
    )


def flatten(src: str) -> str:
    """The paper with the portal's macros resolved, for a publisher's template:
    units become their text, sections become ``\\section``, placement markers go,
    and an unfilled slot stays visible so nobody submits around it."""
    out = src.replace("\\usepackage{sahalekhana}\n", "")
    while True:
        units = parse_units(out)
        if not units:
            break
        unit = units[0]
        out = out[:unit.start] + unit.body + out[unit.end:]
    # Titles and labels are short and brace-free in practice; units above needed
    # the balanced reader because their bodies are not.
    out = re.sub(r"\\adhikarana\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\\section{\2}\\label{\1}", out)
    out = re.sub(r"\\ask\s*\{[^{}]*\}\s*", "", out)
    out = re.sub(r"\\owed\s*\{([^{}]*)\}", r"\\textbf{[owed: \1]}", out)
    return out


_SENSITIVE = {"not", "no", "never", "only", "all", "none", "always", "some", "must",
              "may", "cannot", "can't", "without", "neither", "nor", "every", "most"}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w'’]+", text.lower())


def meaning_changed(old: str, new: str) -> bool:
    """A SUGGESTION, confirmed by the writer at commit: does this edit change what
    the text asserts, or only its form? Quantifiers, negations and numbers are
    treated as meaning-bearing; otherwise a >25% shift in content words is."""
    a, b = _tokens(old), _tokens(new)
    if Counter(a) == Counter(b):
        return False
    def sensitive(ts):
        return Counter(t for t in ts if t in _SENSITIVE or any(ch.isdigit() for ch in t))
    if sensitive(a) != sensitive(b):
        return True
    ca, cb = {t for t in a if len(t) > 3}, {t for t in b if len(t) > 3}
    union = ca | cb
    return bool(union) and len(ca ^ cb) / len(union) > 0.25


def _outside(src: str, units: list[Unit]) -> str:
    parts, pos = [], 0
    for u in units:
        parts.append(src[pos:u.start])
        pos = u.end
    parts.append(src[pos:])
    return "\x00".join(parts)


def changes(old: str, new: str) -> list[Change]:
    old_units = {u.id: u for u in parse_units(old)}
    new_list = parse_units(new)
    new_units = {u.id: u for u in new_list}
    out: list[Change] = []
    for u in new_list:
        before = old_units.get(u.id)
        if before is None:
            out.append(Change("unit:" + u.id, u.id, u.yukti, "added", "", u.body, True))
        elif before.body != u.body or before.yukti != u.yukti:
            suggested = before.yukti != u.yukti or meaning_changed(before.body, u.body)
            out.append(Change("unit:" + u.id, u.id, u.yukti, "changed", before.body, u.body, suggested))
    for uid, u in old_units.items():
        if uid not in new_units:
            out.append(Change("unit:" + uid, uid, u.yukti, "removed", u.body, "", True))
    old_out, new_out = _outside(old, list(old_units.values())), _outside(new, new_list)
    if old_out != new_out:
        a, b = old_out.replace("\x00", " "), new_out.replace("\x00", " ")
        out.append(Change("outside", "", "", "outside", a, b, meaning_changed(a, b)))
    return out


_UNESCAPE = [(r"\textbackslash{}", "\\"), (r"\textasciitilde{}", "~"), (r"\textasciicircum{}", "^"),
             (r"\&", "&"), (r"\%", "%"), (r"\$", "$"), (r"\#", "#"), (r"\_", "_"), (r"\{", "{"), (r"\}", "}")]


def to_plain(tex: str) -> str:
    """Readable text from a unit body, for reviewers. Not a LaTeX renderer: owed
    slots become ``[owed: …]``, simple commands keep their argument, the rest drop."""
    try:
        owed = parse_owed(tex)
    except LatexError:
        owed = []
    for o in reversed(owed):
        tex = tex[:o.start] + "[owed: " + o.text + "]" + tex[o.end:]
    tokens = {}
    for i, (escaped, char) in enumerate(_UNESCAPE):
        token = f"\x00{i}\x00"
        tex = tex.replace(escaped, token)
        tokens[token] = char
    tex = tex.replace("\\\\", " ")
    for _ in range(5):
        stripped = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", tex)
        if stripped == tex:
            break
        tex = stripped
    tex = re.sub(r"\\[A-Za-z]+\*?", "", tex).replace("~", " ").replace("{", "").replace("}", "")
    for token, char in tokens.items():
        tex = tex.replace(token, char)
    return " ".join(tex.split())


def added_owed(old: str, new: str) -> list[str]:
    """Owed slots present in ``new`` but not in ``old`` (with multiplicity)."""
    diff = Counter(o.text for o in parse_owed(new)) - Counter(o.text for o in parse_owed(old))
    return list(diff.elements())
