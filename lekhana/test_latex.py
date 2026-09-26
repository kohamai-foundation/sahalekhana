from django.test import SimpleTestCase

from lekhana import latex

DOC = r"""\documentclass{article}
\usepackage{sahalekhana}
\begin{document}
\adhikarana{sec:why}{Why construe first}
\yukti{why.claim}{pratijna}{Verse orders words for metre, not {syntax}.} \yukti{why.reason}{hetvartha}{Case endings carry the relations, 100\% of the time \{mostly\}.}
% \yukti{commented}{upamana}{ignored}

\ask{why.analogy}
\owed{a citation for word order}
\end{document}
"""


class ParseTests(SimpleTestCase):
    def test_units_read_nested_and_escaped_braces_and_skip_comments(self):
        units = latex.parse_units(DOC)
        self.assertEqual([u.id for u in units], ["why.claim", "why.reason"])
        self.assertEqual(units[0].body, "Verse orders words for metre, not {syntax}.")
        self.assertIn(r"\{mostly\}", units[1].body)
        self.assertEqual(units[0].yukti, "pratijna")

    def test_sections_and_owed(self):
        self.assertEqual(latex.parse_sections(DOC)[0].title, "Why construe first")
        self.assertEqual([o.text for o in latex.parse_owed(DOC)], ["a citation for word order"])

    def test_duplicate_ids_are_refused_with_a_line_number(self):
        with self.assertRaisesRegex(latex.LatexError, r"line 2: unit id 'a' is used twice"):
            latex.parse_units("\\yukti{a}{x}{one}\n\\yukti{a}{x}{two}")

    def test_unclosed_brace_is_reported(self):
        with self.assertRaisesRegex(latex.LatexError, "unclosed brace"):
            latex.parse_units("\\yukti{a}{x}{never closed")

    def test_nested_units_are_refused(self):
        with self.assertRaisesRegex(latex.LatexError, "inside another"):
            latex.parse_units("\\yukti{a}{x}{outer \\yukti{b}{y}{inner}}")


class EditTests(SimpleTestCase):
    def test_replace_body_keeps_everything_else(self):
        unit = latex.parse_units(DOC)[0]
        out = latex.replace_body(DOC, unit, "New claim.")
        self.assertIn(r"\yukti{why.claim}{pratijna}{New claim.}", out)
        self.assertIn(r"\yukti{why.reason}", out)

    def test_new_unit_replaces_its_ask_marker(self):
        out = latex.insert_unit(DOC, "why.analogy", "upamana", "Like an interpreter.")
        self.assertIn(r"\yukti{why.analogy}{upamana}{Like an interpreter.}", out)
        self.assertNotIn(r"\ask{why.analogy}", out)

    def test_new_unit_without_marker_goes_before_end_document(self):
        out = latex.insert_unit(DOC, "why.limit", "apavarga", "Not prose.")
        self.assertLess(out.index(r"\yukti{why.limit}"), out.index(r"\end{document}"))

    def test_candidate_text_is_escaped_and_owed_markers_become_slots(self):
        body, owed = latex.render_candidate("Costs $5 & 50% [[owed: price source]] of_it")
        self.assertEqual(body, r"Costs \$5 \& 50\% \owed{price source} of\_it")
        self.assertEqual(owed, 1)

    def test_to_plain_is_readable(self):
        self.assertEqual(latex.to_plain(r"Case endings, 100\% \emph{always} \owed{cite}."),
                         "Case endings, 100% always [owed: cite].")


class ChangeTests(SimpleTestCase):
    def test_punctuation_only_edit_is_suggested_as_form(self):
        old = "\\yukti{a}{x}{Verse orders words for metre}"
        found = latex.changes(old, "\\yukti{a}{x}{Verse orders words, for metre.}")
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].suggested_meaning)

    def test_a_negation_is_suggested_as_meaning(self):
        found = latex.changes("\\yukti{a}{x}{It helps.}", "\\yukti{a}{x}{It does not help.}")
        self.assertTrue(found[0].suggested_meaning)

    def test_added_removed_and_outside_changes(self):
        old = "Intro. \\yukti{a}{x}{One.} \\yukti{b}{x}{Two.}"
        new = "Intro changed a lot here. \\yukti{a}{x}{One.} \\yukti{c}{x}{Three.}"
        found = {c.key: c.status for c in latex.changes(old, new)}
        self.assertEqual(found, {"unit:c": "added", "unit:b": "removed", "outside": "outside"})

    def test_added_owed_counts_multiplicity(self):
        self.assertEqual(latex.added_owed("\\owed{x}", "\\owed{x} \\owed{x} \\owed{y}"), ["x", "y"])
