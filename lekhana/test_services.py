"""The record: every change lands in git as typed acts, and nothing is lost."""
import json
from unittest import mock

from django.core.management import call_command
from django.test import override_settings

from lekhana import ai, ledger, services
from lekhana.models import Act, Anchor, Ask, Candidate
from lekhana.testing import PROPOSALS, RepoTestCase

THESIS = "State the paper's thesis in one sentence."


class CreateAndSaveTests(RepoTestCase):
    def test_creating_a_project_commits_the_scaffold_with_a_frame_act(self):
        self.assertIn(r"\usepackage{sahalekhana}", self.src())
        self.assertTrue((self.path / "sahalekhana.sty").exists())
        entry = self.last()
        self.assertIn(("Sahalekhana", "v0"), entry["trailers"])
        self.assertRegex(self.act_lines(entry)[0], r"^1 frame agent=human:ram role=svatantra")
        self.assertEqual(Act.objects.get(kind="frame").commit_sha, entry["sha"])

    def test_a_confirmed_meaning_edit_is_a_draft(self):
        self.edit(THESIS, "Construing a verse first helps its translation.")
        self.assertIn("Construing a verse first", self.src())
        self.assertRegex(self.act_lines()[0], r"draft agent=human:ram role=svatantra yukti=uddesha "
                                              r"unit=intro.thesis meaning=yes")

    def test_an_unconfirmed_edit_is_a_rephrase(self):
        self.edit(THESIS, "State the paper's thesis, in one sentence.", meaning=())
        self.assertIn("rephrase", self.act_lines()[0])
        self.assertIn("meaning=no", self.act_lines()[0])

    def test_a_stale_base_that_changed_the_paper_is_a_conflict_and_writes_nothing(self):
        stale = services.head(self.project)
        self.edit(THESIS, "First version.")
        head, acts = services.head(self.project), Act.objects.count()
        with self.assertRaises(services.Conflict):
            services.save(project=self.project, user=self.author, content=self.src().replace("First", "Second"),
                          base_sha=stale, message="", meaning=set())
        self.assertEqual((services.head(self.project), Act.objects.count()), (head, acts))

    def test_adding_an_owed_slot_records_a_reserve(self):
        self.edit(THESIS, THESIS + r" \owed{a recent citation}", meaning=())
        self.assertTrue(any(line.split()[1] == "reserve" for line in self.act_lines()))

    def test_nothing_changed_is_refused(self):
        with self.assertRaises(services.InvalidAction):
            self.edit(THESIS, THESIS)


class AskTests(RepoTestCase):
    def test_ask_records_the_instruction_and_each_candidate_as_pending(self):
        thread = self.ask()
        self.assertEqual(thread.candidates.count(), 3)
        instruct = Act.objects.get(kind="instruct")
        self.assertEqual((instruct.role, instruct.agent), ("prayojaka", "human:ram"))
        drafts = Act.objects.filter(kind="draft", agent="ai:gpt-5.5", role="prayojya")
        self.assertEqual(drafts.count(), 3)
        self.assertFalse(Act.objects.filter(ask=thread).exclude(commit_sha="").exists())

    def test_objection_candidates_are_dialectical(self):
        self.ask(device="purvapaksha")
        self.assertEqual(Act.objects.filter(kind="object").count(), 3)

    def test_a_failed_ai_keeps_the_instruction_and_marks_the_thread_failed(self):
        with mock.patch.object(services.ai, "propose", side_effect=ai.AIUnavailable("not configured")):
            thread = services.ask(project=self.project, user=self.author, unit_id="intro.thesis",
                                  device="upamana", count=3, instruction="x", constraint="")
        self.assertEqual((thread.status, thread.error), (Ask.Status.FAILED, "not configured"))
        self.assertTrue(Act.objects.filter(kind="instruct", ask=thread).exists())
        self.assertFalse(thread.candidates.exists())

    def test_bad_unit_id_is_refused(self):
        with self.assertRaises(services.InvalidAction):
            self.ask(unit="has spaces")


class DecideTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.thread = self.ask()
        self.a, self.b, self.c = self.thread.candidates.order_by("index")

    def test_examined_accept_is_affirm_and_carries_the_rejection_into_the_same_commit(self):
        services.reject(self.a, self.author, reason="drishtantabhasa_no_shared_property")
        services.examine(self.b, self.author)
        sha = services.accept(self.b, self.author)
        lines = self.act_lines()
        self.assertTrue(any("flag_defect" in l and "defect=drishtantabhasa_no_shared_property" in l
                            and "candidate=a" in l for l in lines))
        self.assertTrue(any(l.split()[1] == "affirm" and "unit=intro.thesis.upamana" in l for l in lines))
        self.assertIn(r"\yukti{intro.thesis.upamana}{upamana}{A court interpreter", self.src())
        record = json.loads((self.path / f".sahalekhana/vimarsha/{self.thread.pk}.json").read_text())
        self.assertEqual([c["status"] for c in record["candidates"]], ["rejected", "accepted", "proposed"])
        self.assertEqual(Act.objects.filter(ask=self.thread, commit_sha=sha).count(), 6)

    def test_unexamined_accept_is_recorded_as_such_and_not_in_the_profile(self):
        services.accept(self.b, self.author)
        self.assertTrue(Act.objects.filter(kind="unexamined_assent").exists())
        _, unexamined = services.profile(self.project)
        self.assertEqual(unexamined, 1)
        self.assertEqual(services.unit_origins(self.project)["intro.thesis.upamana"]["origin"], "ai_unexamined")

    def test_owed_marker_becomes_a_slot_reserved_by_the_ai(self):
        services.examine(self.c, self.author)
        services.accept(self.c, self.author)
        self.assertIn(r"\owed{evidence that inverted order slows readers}", self.src())
        self.assertIn(r"50\% of Yoda's lines \& more", self.src())
        self.assertTrue(Act.objects.filter(kind="reserve", agent="ai:gpt-5.5").exists())

    def test_same_device_on_an_existing_unit_rewrites_it(self):
        self.edit(THESIS + "}", THESIS + r"} \yukti{thesis.analogy}{upamana}{placeholder}", meaning=())
        with self.propose():
            thread = services.ask(project=self.project, user=self.author, unit_id="thesis.analogy",
                                  device="upamana", count=3, instruction="", constraint="")
        cand = thread.candidates.get(index=2)
        services.examine(cand, self.author)
        services.accept(cand, self.author)
        self.assertIn(r"\yukti{thesis.analogy}{upamana}{A court interpreter", self.src())
        self.assertNotIn("placeholder", self.src())

    def test_a_new_unit_goes_to_its_ask_marker(self):
        self.edit(THESIS + "}", THESIS + "}\n\n\\ask{intro.example}", meaning=())
        with self.propose():
            thread = services.ask(project=self.project, user=self.author, unit_id="intro.example",
                                  device="nidarshana", count=3, instruction="", constraint="")
        services.accept(thread.candidates.get(index=1), self.author)
        self.assertIn(r"\yukti{intro.example}{nidarshana}{Sorting a shuffled deck", self.src())
        self.assertNotIn(r"\ask{intro.example}", self.src())

    def test_an_ask_marker_inside_a_unit_is_refused_and_nothing_is_committed(self):
        self.edit(THESIS, THESIS + " \\ask{intro.example}", meaning=())
        with self.propose():
            thread = services.ask(project=self.project, user=self.author, unit_id="intro.example",
                                  device="nidarshana", count=3, instruction="", constraint="")
        head = services.head(self.project)
        with self.assertRaisesRegex(services.InvalidAction, "move the marker outside it"):
            services.accept(thread.candidates.get(index=1), self.author)
        self.assertEqual(services.head(self.project), head)
        self.assertEqual(thread.candidates.get(index=1).status, Candidate.Status.PROPOSED)

    def test_other_needs_a_note_and_a_decision_is_final(self):
        with self.assertRaises(services.InvalidAction):
            services.reject(self.a, self.author, reason="other")
        services.reject(self.a, self.author, reason="register")
        self.a.refresh_from_db()
        with self.assertRaises(services.InvalidAction):
            services.accept(self.a, self.author)


class ReviewTests(RepoTestCase):
    def test_a_reviewer_ask_is_committed_and_never_touches_the_paper(self):
        sita = self.reviewer()
        before = self.src()
        with self.propose([{"text": "The paper claims construal helps (intro.thesis).", "check": "Stated only."}]):
            thread = services.review_ask(project=self.project, user=sita, unit_id="", device="claims", instruction="")
        self.assertEqual(thread.status, Ask.Status.RESOLVED)
        self.assertEqual(self.src(), before)
        lines = self.act_lines()
        self.assertTrue(any("review_ask" in l and "role=parikshaka" in l for l in lines))
        self.assertTrue(any(l.split()[1] == "explain" and "agent=ai:gpt-5.5" in l for l in lines))
        self.assertTrue((self.path / f".sahalekhana/vimarsha/{thread.pk}.json").exists())
        with self.assertRaises(services.InvalidAction):
            services.accept(thread.candidates.first(), self.author)

    def test_grant_is_an_act_and_a_reviewer_cannot_write(self):
        sita = self.reviewer()
        self.assertTrue(any("grant" in l for l in self.act_lines()))
        self.assertFalse(services.can_write(sita, self.project))
        self.assertTrue(services.can_access(sita, self.project))

    def test_points_and_responses_are_committed(self):
        sita = self.reviewer()
        point = services.add_point(project=self.project, user=sita, unit_id="intro.thesis",
                                   kind="objection", text="Helps whom: humans or models?")
        self.assertTrue(any("review_point" in l and f"point={point.pk}" in l for l in self.act_lines()))
        services.respond(point, self.author, text="Models; tested in section 5.")
        record = json.loads((self.path / f".sahalekhana/review/{point.pk}.json").read_text())
        self.assertEqual(record["responded_by"], "ram")
        with self.assertRaises(services.InvalidAction):
            services.respond(point, self.author, text="again")

    def test_a_review_commit_does_not_block_an_open_edit(self):
        base = services.head(self.project)
        sita = self.reviewer()
        services.add_point(project=self.project, user=sita, unit_id="", kind="question", text="Scope?")
        services.save(project=self.project, user=self.author, content=self.src().replace(THESIS, "Edited."),
                      base_sha=base, message="", meaning={"unit:intro.thesis"})
        self.assertIn("Edited.", self.src())

    def test_unit_history_shows_each_version_newest_first_with_its_acts(self):
        self.edit(THESIS, "Construing a verse first helps its translation.")
        self.edit("first helps", "first may help")
        history = services.unit_history(self.project, "intro.thesis")
        texts = [v["text"] for v in history["versions"]]
        self.assertEqual(texts, ["Construing a verse first may help its translation.",
                                 "Construing a verse first helps its translation.", THESIS])
        self.assertEqual(history["versions"][0]["acts"][0].kind, "draft")

    def test_paper_blocks_keep_paragraphs_and_origins(self):
        thread = self.ask()
        cand = thread.candidates.get(index=2)
        services.examine(cand, self.author)
        services.accept(cand, self.author)
        blocks = services.paper_blocks(self.src(), services.unit_origins(self.project))
        self.assertEqual(blocks[0], {"type": "section", "title": "Introduction"})
        units = blocks[1]["units"]
        self.assertEqual([u["id"] for u in units], ["intro.thesis", "intro.thesis.upamana"])
        self.assertEqual(units[1]["origin"], "ai_examined")


class AnchorTests(RepoTestCase):
    def test_anchor_folds_heads_and_skips_when_nothing_moved(self):
        call_command("anchor_lekhana", stdout=open("/dev/null", "w"))
        anchor = Anchor.objects.get()
        head = services.head(self.project)
        self.assertEqual(anchor.heads, [["anvaya-first", head]])
        self.assertEqual(anchor.root_hash, ledger.anchor_root([("anvaya-first", head)]))
        call_command("anchor_lekhana", stdout=open("/dev/null", "w"))
        self.assertEqual(Anchor.objects.count(), 1)
        self.edit(THESIS, "Moved.")
        call_command("anchor_lekhana", stdout=open("/dev/null", "w"))
        self.assertEqual(Anchor.objects.count(), 2)
