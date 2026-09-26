"""The vocabulary every act on the portal is typed by: tantrayukti devices, the
moves a writer or reviewer can ask the AI for, act kinds, roles, and reasons.

This is the code-side mirror of ``docs/sahalekhana/sahalekhana.ttl``. The
ontology is the reference; this module holds only what the portal uses, as
plain data, so views, services and git trailers share one vocabulary. The
yukti list stays OPEN: ``\\yukti{id}{device}{text}`` may name a device outside
these 32 (pratijñā, prayojana, …) and is recorded as written.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Yukti:
    code: str
    iast: str
    family: str
    gloss: str


FAMILIES = {
    "framing": "Framing & arrangement",
    "meaning": "Meaning & terms",
    "evidence": "Reason & evidence",
    "scope": "Rule & scope",
    "dialectic": "Dialectic",
    "reference": "Cross-reference",
}

# Arthaśāstra 15.1 — the 32, grouped by what they do in a modern paper.
YUKTIS = [
    Yukti("adhikarana", "adhikaraṇa", "framing", "the subject matter"),
    Yukti("vidhana", "vidhāna", "framing", "arrangement of topics"),
    Yukti("yoga", "yoga", "framing", "joining words into a statement"),
    Yukti("uddesha", "uddeśa", "framing", "brief statement"),
    Yukti("nirdesha", "nirdeśa", "framing", "detailed exposition"),
    Yukti("padartha", "padārtha", "meaning", "meaning of a word"),
    Yukti("nirvacana", "nirvacana", "meaning", "etymological derivation"),
    Yukti("svasamjna", "svasaṃjñā", "meaning", "a technical term of one's own"),
    Yukti("vyakhyana", "vyākhyāna", "meaning", "explanation"),
    Yukti("vakyashesha", "vākyaśeṣa", "meaning", "completing an elliptical statement"),
    Yukti("uhya", "ūhya", "meaning", "to be worked out by reasoning"),
    Yukti("hetvartha", "hetvartha", "evidence", "the purpose of a reason"),
    Yukti("upamana", "upamāna", "evidence", "analogy"),
    Yukti("nidarshana", "nidarśana", "evidence", "illustration by example"),
    Yukti("arthapatti", "arthāpatti", "evidence", "implication"),
    Yukti("apadesha", "apadeśa", "evidence", "citing what another said"),
    Yukti("anumata", "anumata", "evidence", "another's view, accepted"),
    Yukti("upadesha", "upadeśa", "scope", "direct instruction"),
    Yukti("atidesha", "atideśa", "scope", "extending a statement to another case"),
    Yukti("prasanga", "prasaṅga", "scope", "applying what was said in a related context"),
    Yukti("apavarga", "apavarga", "scope", "exception to a rule"),
    Yukti("ekanta", "ekānta", "scope", "an invariable rule"),
    Yukti("niyoga", "niyoga", "scope", "restriction: only this"),
    Yukti("vikalpa", "vikalpa", "scope", "option, alternative"),
    Yukti("samuccaya", "samuccaya", "scope", "taking together"),
    Yukti("viparyaya", "viparyaya", "scope", "the converse"),
    Yukti("samshaya", "saṃśaya", "dialectic", "doubt between alternatives"),
    Yukti("purvapaksha", "pūrvapakṣa", "dialectic", "the opposing view"),
    Yukti("uttarapaksha", "uttarapakṣa", "dialectic", "the reply"),
    Yukti("pradesha", "pradeśa", "reference", "pointer to elsewhere"),
    Yukti("anagatavekshana", "anāgatāvekṣaṇa", "reference", "looking ahead"),
    Yukti("atikrantavekshana", "atikrāntāvekṣaṇa", "reference", "looking back"),
]
YUKTI = {y.code: y for y in YUKTIS}


def iast(code: str) -> str:
    """The IAST name of a device, or the code as written when it is outside the 32."""
    y = YUKTI.get(code)
    return y.iast if y else code


@dataclass(frozen=True)
class Device:
    """A move someone can ask the AI to make. ``ask`` is the instruction the
    model receives; ``check`` is what it must hand back so the person can test
    the answer. ``code`` names the device in the UI; ``yukti`` types the act."""
    yukti: str
    label: str
    ask: str
    check: str
    code: str = ""

    def __post_init__(self):
        if not self.code:
            object.__setattr__(self, "code", self.yukti)

    @property
    def iast(self) -> str:
        return iast(self.yukti)


# What an AUTHOR can ask for. Accepted candidates are placed in the paper.
ASK_DEVICES = [
    Device("upamana", "An analogy",
           "Offer an analogy: a better-understood case that shares the property the point depends on.",
           "Name the shared property the analogy relies on, show that it holds in both cases, "
           "and say where the analogy stops holding."),
    Device("nidarshana", "An example",
           "Offer a concrete worked example, case or data point that illustrates the point.",
           "Say exactly what the example instantiates, and whether it is real (with a source the "
           "author can check) or constructed for illustration."),
    Device("purvapaksha", "The strongest objection",
           "State the strongest objection to the unit's claim, as its best proponent would put it.",
           "Say what the objection targets (the claim, the reason or the evidence) and what would "
           "count as answering it."),
    Device("uttarapaksha", "A reply to an objection",
           "Reply to the objection in or near the unit, without quietly weakening the claim.",
           "Restate the objection being answered in one line, then say which part of it the reply "
           "concedes, if any."),
    Device("hetvartha", "A reason",
           "Give a reason that supports the unit's claim.",
           "Say what the reason assumes, and whether it would equally support the opposite conclusion."),
    Device("padartha", "A definition of a term",
           "Define the key term as this paper uses it.",
           "Say how this sense differs from the term's common or neighbouring senses."),
    Device("apavarga", "A limitation or exception",
           "State a limitation, boundary condition or exception to the claim.",
           "Say whether the exception narrows the claim or contradicts it."),
    Device("vakyashesha", "The unstated assumptions",
           "State the assumptions the unit relies on but does not say.",
           "For each assumption, say whether a reviewer is likely to contest it."),
    Device("vyakhyana", "A plainer explanation",
           "Explain the unit's point in plainer words for a reader outside the field.",
           "Say what, if anything, the plainer version loses or changes."),
    Device("samuccaya", "A synthesis",
           "Combine the points already made in this section into one statement.",
           "Say which earlier units each part of the synthesis draws on."),
]
DEVICE = {d.code: d for d in ASK_DEVICES}

# What a REVIEWER can ask the paper. Answers are recorded, never placed in it.
REVIEW_DEVICES = [
    Device("upamana", "An analogy that explains this part",
           "Offer an analogy that would help a reviewer understand this part of the paper.",
           "Name the shared property the analogy relies on and where it stops holding, so the "
           "reviewer does not read more into the paper than it says.", code="explain_analogy"),
    Device("nidarshana", "An example that explains this part",
           "Offer a concrete example that would help a reviewer understand this part of the paper.",
           "Say whether the example comes from the paper itself (give the unit id) or was constructed "
           "to explain it.", code="explain_example"),
    Device("vyakhyana", "A plainer explanation",
           "Explain this part of the paper in plainer words.",
           "Say what the plainer version leaves out, so the reviewer can go back to the paper for it.",
           code="explain_plain"),
    Device("purvapaksha", "The strongest objection to it",
           "State the strongest objection a careful reviewer could raise against this part of the paper.",
           "Say whether the paper already answers it, and in which unit.", code="objection"),
    Device("", "A consistency check",
           "Find places where the paper disagrees with itself: a claim, definition or number stated one "
           "way in one unit and differently in another, or a promise (such as 'as we show in §5') the "
           "paper does not keep. If you find none, return one candidate saying so and what you checked.",
           "Quote both places by unit id and say why they conflict, or why the apparent conflict is not one.",
           code="consistency"),
    Device("uddesha", "The paper's claims",
           "List the paper's claims, one per candidate, each with the unit ids that state it.",
           "Say whether the paper supports the claim, and by which units.", code="claims"),
    Device("nirdesha", "The paper's methods",
           "Describe the paper's methods, one method per candidate, citing unit ids.",
           "Say what a reader would need in order to reproduce it that the paper does not give.",
           code="methods"),
    Device("apadesha", "The background it builds on",
           "List the prior work and background the paper builds on, one per candidate, citing unit ids. "
           "Name only works the paper itself names; do not add sources.",
           "Say which units rely on it and whether the paper cites it.", code="background"),
]
REVIEW_DEVICE = {d.code: d for d in REVIEW_DEVICES}


@dataclass(frozen=True)
class Stance:
    """The role the writer gives the AI in discussion, and what it may not do.

    Restrictions are enforced in two ways: the tools a stance withholds are
    simply absent from the request (structural), and the rules are stated in
    the system prompt (asked). What the AI may never do under any stance is
    write to the paper: no tool can. Every turn records the stance in force."""
    code: str
    label: str
    summary: str
    may_draft: bool = False          # may propose candidate text, via the ask panel
    may_suggest_next_steps: bool = False
    questions_only: bool = False
    max_sentences: int = 0           # 0 = no cap
    rules: tuple = ()


_NEVER = ("You never write to the paper. Only the author places text in it.",)

STANCES = [
    Stance("interlocutor", "Interlocutor",
           "Asks and objects. Never drafts, never tells you what to do next.",
           max_sentences=8,
           rules=("Do not draft sentences for the paper, even as an illustration.",
                  "Do not propose a plan or a next step. The writer decides the order of work.",
                  "Prefer one good question or objection to a survey of several.")),
    Stance("socratic", "Socratic",
           "Replies only with questions.",
           questions_only=True, max_sentences=5,
           rules=("Reply only with questions. No statements, no summaries, no advice.",)),
    Stance("examiner", "Examiner",
           "Names defects, gaps and unstated assumptions. Supplies no replacement text.",
           max_sentences=10,
           rules=("Examine what is written: name defects, gaps and assumptions the paper leaves unstated.",
                  "Do not supply replacement wording. Say what is wrong, not what to write instead.")),
    Stance("devils_advocate", "Devil's advocate",
           "Argues the other side at its strongest, whatever it privately thinks.",
           max_sentences=10,
           rules=("Argue against the paper's position as its best-informed opponent would.",
                  "Do not concede for politeness. If the objection fails, say exactly where it fails.")),
    Stance("scribe", "Scribe",
           "Drafts only when asked, through the ask panel. Volunteers nothing.",
           may_draft=True, max_sentences=6,
           rules=("Propose wording only when the writer asks for it, and only through propose_candidates.",
                  "Never put draft prose in the chat itself.")),
    Stance("partner", "Partner",
           "May draft, may suggest what to do next. The fewest restrictions.",
           may_draft=True, may_suggest_next_steps=True, max_sentences=12),
]
STANCE = {s.code: s for s in STANCES}
DEFAULT_STANCE = "interlocutor"


def stance_rules(stance: Stance) -> tuple:
    return _NEVER + stance.rules + (
        () if stance.may_suggest_next_steps else
        ("Do not end with suggestions, next steps, or an offer to do more.",))


# What a recorded idea is. Attribution is a claim until a person confirms it.
IDEA_KINDS = [
    ("idea", "Idea"),
    ("framing", "Framing"),
    ("term", "Term"),
    ("question", "Question"),
    ("objection", "Objection"),
    ("method", "Method"),
    ("example", "Example"),
]


# Act kinds: (code, label, family). Family None = recorded, never acknowledged.
ACT_KINDS = [
    ("frame", "Frame the work", "directive"),
    ("instruct", "Instruct the AI", "directive"),
    ("say", "Say something in discussion", "discussion"),
    ("answer", "Answer in discussion", "discussion"),
    ("record_idea", "Record an idea", "discussion"),
    ("attribute", "Confirm or correct an attribution", "examining"),
    ("set_stance", "Set the AI's role", "ledger"),
    ("set_tracking", "Change composition tracking", "ledger"),
    ("method_start", "Start a writing method", "directive"),
    ("method_step", "Complete a method step", "directive"),
    ("compile", "Compile the PDF", "ledger"),
    ("export", "Export the paper", "ledger"),
    ("draft", "Draft (changes what the text asserts)", "generative"),
    ("object", "Object (pūrvapakṣa)", "dialectical"),
    ("rebut", "Reply (uttarapakṣa)", "dialectical"),
    ("respond", "Respond to a review point", "dialectical"),
    ("rephrase", "Rephrase (form only)", "editorial"),
    ("affirm", "Accept after examining", "examining"),
    ("flag_defect", "Reject, naming a defect", "examining"),
    ("reject", "Reject on other grounds", "examining"),
    ("reserve", "Reserve a slot", "examining"),
    ("review_ask", "Ask the paper while reviewing", "review"),
    ("explain", "Answer a reviewer's ask", "review"),
    ("review_point", "Raise a review point", "review"),
    ("grant", "Grant access", "ledger"),
    ("unexamined_assent", "Accept without examining", None),
]
ACT_FAMILY = {code: family for code, _, family in ACT_KINDS}
ACT_LABEL = {code: label for code, label, _ in ACT_KINDS}
ACT_FAMILIES = {
    "directive": "Directive",
    "generative": "Generative",
    "examining": "Examining",
    "dialectical": "Dialectical",
    "editorial": "Editorial",
    "discussion": "Discussion",
    "review": "Review",
}

# Pāṇini's kāraka roles, as the portal uses them. A role belongs to an act.
ROLES = [
    ("svatantra", "Independent (svatantra)"),
    ("prayojaka", "Directing (prayojaka)"),
    ("prayojya", "Directed (prayojya)"),
    ("sakshin", "Examiner (sākṣin)"),
    ("parikshaka", "Reviewer (parīkṣaka)"),
]

MEMBER_ROLES = [("author", "Author"), ("reviewer", "Reviewer")]

REVIEW_POINT_KINDS = [
    ("question", "Question"),
    ("objection", "Objection"),
    ("inconsistency", "Inconsistency"),
    ("suggestion", "Suggestion"),
    ("strength", "Strength"),
]


@dataclass(frozen=True)
class Reason:
    code: str
    label: str
    defect: bool  # True = a defect of reasoning (flag_defect); False = other grounds (reject)


REJECT_REASONS = [
    Reason("drishtantabhasa_no_shared_property", "Pseudo-example: the shared property doesn't hold", True),
    Reason("drishtantabhasa_proves_too_much", "Pseudo-example: it would support the opposite too", True),
    Reason("sadhyasama", "Assumes what it should prove", True),
    Reason("savyabhicara", "Inconclusive: fits the opposite conclusion too", True),
    Reason("viruddha", "Contradicts the claim it supports", True),
    Reason("kalatita", "Outdated or superseded", True),
    Reason("unverifiable_source", "Relies on a source that can't be verified", True),
    Reason("arthantara", "Off the point", True),
    Reason("ananubhashana", "Answers a straw version of the objection", True),
    Reason("apasiddhanta", "Contradicts a position fixed earlier in the paper", True),
    Reason("adhika", "Padding: adds nothing", True),
    Reason("punarukta", "Repeats what the paper already says", True),
    Reason("constraint", "Breaks the constraint I gave", False),
    Reason("register", "Wrong register or tone", False),
    Reason("other", "Other (say why)", False),
]
REASON = {r.code: r for r in REJECT_REASONS}
