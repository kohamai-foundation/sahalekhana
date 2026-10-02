"""Leaving the portal: the flattened source, the generated declaration, the export."""
import io
import json
import zipfile
from unittest import mock

from django.test import SimpleTestCase, override_settings

from lekhana import build, latex, services
from lekhana.models import Act
from lekhana.testing import PROPOSALS, RepoTestCase

THESIS = "State the paper's thesis in one sentence."


class FlattenTests(SimpleTestCase):
    def test_macros_resolve_to_ordinary_latex(self):
        source = ("\\documentclass{article}\n\\usepackage{sahalekhana}\n\\begin{document}\n"
                  "\\adhikarana{sec:why}{Why construe}\n"
                  "\\yukti{why.claim}{pratijna}{Word order is free.} \\ask{why.example}\n"
                  "\\yukti{why.limit}{apavarga}{Not prose \\owed{a citation}.}\n\\end{document}\n")
        out = latex.flatten(source)
        self.assertIn("\\section{Why construe}\\label{sec:why}", out)
        self.assertIn("Word order is free.", out)
        self.assertNotIn("\\yukti", out)
        self.assertNotIn("\\ask{", out)
        self.assertNotIn("\\usepackage{sahalekhana}", out)
        self.assertIn("\\textbf{[owed: a citation]}", out)


class ExportTests(RepoTestCase):
    def _work(self):
        self.edit(THESIS, "Construing a verse first helps its translation.")
        thread = self.ask()
        first, second, third = thread.candidates.order_by("index")
        services.reject(first, self.author, reason="drishtantabhasa_no_shared_property")
        services.examine(second, self.author)
        services.accept(second, self.author)
        self.edit("translation.}", "translation.} \\owed{a recent study}", meaning=())

    def test_the_declaration_is_generated_from_the_record(self):
        self._work()
        text = build.declaration(self.project)
        self.assertIn("Generated from this paper's act record", text)
        self.assertIn("gpt-5.5", text)
        self.assertIn("accepted after examination: 1", text)
        self.assertIn("rejected: 1", text)
        self.assertIn("Grounds given for rejection: Pseudo-example", text)
        self.assertIn("Slots still owed by the authors: 1", text)
        self.assertIn("never wrote to the paper", text)

    def test_unexamined_acceptance_is_named_in_the_declaration(self):
        thread = self.ask()
        services.accept(thread.candidates.first(), self.author)
        self.assertIn("accepted without examination", build.declaration(self.project))

    def test_the_export_carries_source_provenance_and_declaration(self):
        self._work()
        bundle = zipfile.ZipFile(io.BytesIO(build.export_zip(self.project)))
        names = {n.split("/", 1)[1] for n in bundle.namelist()}
        self.assertEqual(names, {"main.tex", "sahalekhana.sty", "main-plain.tex",
                                 "DECLARATION.md", "provenance.json", "README.txt"})
        record = json.loads(bundle.read(f"{self.project.slug}/provenance.json"))
        self.assertEqual(record["paper"]["title"], self.project.title)
        self.assertTrue(any(a["kind"] == "affirm" for a in record["acts"]))
        self.assertTrue(any(a["agent"].startswith("ai:") for a in record["acts"]))

    def test_the_anonymised_export_replaces_names_with_roles(self):
        self._work()
        self.reviewer("sita")
        raw = build.export_zip(self.project, anonymise=True)
        bundle = zipfile.ZipFile(io.BytesIO(raw))
        record = json.loads(bundle.read(f"{self.project.slug}/provenance.json"))
        agents = {a["agent"] for a in record["acts"]}
        self.assertIn("author-1", agents)
        self.assertIn("ai", agents)
        self.assertNotIn("human:ram", agents)
        self.assertFalse(any(a["note"] for a in record["acts"]))

    def test_exporting_is_itself_an_act(self):
        build.record(self.project, self.author, kind="export", note="Exported the paper")
        self.assertTrue(any("export" in line for line in self.act_lines()))

    @override_settings(LEKHANA_LATEX_CMD="definitely-not-a-real-binary")
    def test_without_latex_the_portal_says_so(self):
        self.assertFalse(build.latex_available())
        with self.assertRaisesRegex(build.BuildError, "INSTALL_TEXLIVE=1"):
            build.compile_pdf(self.project)
