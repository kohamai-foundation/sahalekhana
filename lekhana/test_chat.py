"""The discussion: what the AI may do, what it may never do, and what is recorded."""
import json
from unittest import mock

from django.test import override_settings

from lekhana import ai, chat, grammar, methods, services
from lekhana.models import Act, Ask, Conversation, Idea, MethodStep, Turn
from lekhana.testing import RepoTestCase

THESIS = "State the paper's thesis in one sentence."


def reply(text="A question about the claim?", tool_calls=(), model="gpt-5.5"):
    calls = tuple(ai.ToolCall(id=f"tc{i}", name=name, input=payload)
                  for i, (name, payload) in enumerate(tool_calls, 1))
    return ai.Reply(text=text or "", model=model, tool_calls=calls,
                    message={"role": "assistant", "content": text or ""})


class ChatTestCase(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.talk = Conversation.objects.create(project=self.project, title="Why construe",
                                                stance="interlocutor", created_by=self.author)

    def send(self, text="Does the claim hold?", replies=None, stance=None):
        if stance:
            self.talk.stance = stance
            self.talk.save(update_fields=["stance"])
        responses = list(replies or [reply()])
        client = mock.MagicMock()
        with mock.patch.object(chat.ai, "configured", return_value=True), \
             mock.patch.object(chat.ai, "client", return_value=client), \
             mock.patch.object(chat.ai, "call", side_effect=responses) as call:
            turn = chat.send(conversation=self.talk, user=self.author, text=text)
        return turn, call


class DiscussionTests(ChatTestCase):
    def test_both_turns_are_recorded_and_committed(self):
        turn, _ = self.send()
        self.assertEqual([t.speaker for t in self.talk.turns.all()], ["human", "ai"])
        self.assertEqual(turn.text, "A question about the claim?")
        kinds = [line.split()[1] for line in self.act_lines()]
        self.assertIn("say", kinds)
        self.assertIn("answer", kinds)
        self.assertTrue((self.path / f".sahalekhana/samvada/{self.talk.pk}.json").exists())

    def test_the_paper_is_read_through_tools_not_dumped_into_the_prompt(self):
        _, call = self.send(replies=[reply(text=None, tool_calls=[("read_paper", {"unit_id": ""})]),
                                     reply("So the thesis is unsupported?")])
        first = call.call_args_list[0].kwargs
        self.assertNotIn(THESIS, " ".join(str(m) for m in first["messages"]))
        self.assertIn("read_paper", [t["name"] for t in first["tools"]])
        self.assertEqual(self.talk.turns.get(speaker="ai").tool_log[0]["tool"], "read_paper")

    def test_an_unconfigured_ai_still_records_what_the_writer_said(self):
        with mock.patch.object(chat.ai, "configured", return_value=False):
            turn = chat.send(conversation=self.talk, user=self.author, text="Anyone there?")
        self.assertIn("OPENAI_API_KEY", turn.text)
        self.assertEqual(self.talk.turns.filter(speaker="human").count(), 1)

    def test_empty_messages_are_refused(self):
        with self.assertRaises(services.InvalidAction):
            chat.send(conversation=self.talk, user=self.author, text="   ")


class StanceTests(ChatTestCase):
    def test_a_role_without_drafting_is_not_given_the_drafting_tool(self):
        _, call = self.send()
        names = [t["name"] for t in call.call_args_list[0].kwargs["tools"]]
        self.assertNotIn("propose_candidates", names)
        self.assertIn("record_idea", names)

    def test_the_scribe_may_propose_into_the_ask_panel_but_not_into_the_paper(self):
        before = self.src()
        _, call = self.send(stance="scribe", replies=[
            reply(text=None, tool_calls=[("propose_candidates", {
                "unit_id": "intro.thesis", "device": "upamana",
                "candidates": [{"text": "Like an interpreter.", "check": "Roles fixed first."}]})]),
            reply("Two candidates are waiting.")])
        self.assertIn("propose_candidates", [t["name"] for t in call.call_args_list[0].kwargs["tools"]])
        self.assertEqual(self.src(), before)
        thread = Ask.objects.get()
        self.assertEqual(thread.candidates.get().status, "proposed")
        self.assertTrue(Act.objects.filter(kind="draft", agent__startswith="ai:").exists())

    def test_the_rules_reach_the_model_and_the_stance_is_recorded(self):
        _, call = self.send(stance="socratic")
        system = call.call_args_list[0].kwargs["system"]
        self.assertIn("Reply only with questions", system)
        self.assertIn("never write to the paper", system.lower())
        self.assertEqual(self.talk.turns.get(speaker="ai").stance, "socratic")

    def test_a_reply_that_breaks_the_role_is_flagged_not_rewritten(self):
        turn, _ = self.send(stance="socratic", replies=[reply("The claim is fine. Ship it.")])
        self.assertIn("statements", turn.flagged)
        self.assertEqual(turn.text, "The claim is fine. Ship it.")
        self.assertIn("broke:", Act.objects.filter(kind="answer").first().note)

    def test_changing_the_role_is_an_act(self):
        chat.set_stance(conversation=self.talk, user=self.author, stance="examiner")
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.stance, "examiner")
        self.assertTrue(any("set_stance" in line for line in self.act_lines()))

    def test_length_cap_is_measured_against_the_role(self):
        long_reply = " ".join(f"Sentence {i}." for i in range(1, 15))
        turn, _ = self.send(stance="interlocutor", replies=[reply(long_reply)])
        self.assertIn("over the 8", turn.flagged)


class IdeaTests(ChatTestCase):
    def _record(self, origin="writer"):
        self.send(replies=[
            reply(text=None, tool_calls=[("record_idea", {
                "text": "Construal is a separate step from translation.",
                "kind": "framing", "origin": origin, "units": ["intro.thesis"]})]),
            reply("Whose framing was that?")])
        return Idea.objects.get()

    def test_an_idea_records_its_claimed_origin_unconfirmed(self):
        idea = self._record(origin="writer")
        self.assertEqual(idea.origin, "human:ram")
        self.assertIsNone(idea.confirmed_at)
        self.assertEqual(idea.origin_turn, self.talk.turns.get(speaker="ai"))
        self.assertTrue(any("record_idea" in line for line in self.act_lines()))

    def test_a_person_can_correct_the_attribution_and_it_is_an_act(self):
        idea = self._record(origin="writer")
        chat.attribute(idea=idea, user=self.author, origin="ai")
        idea.refresh_from_db()
        self.assertTrue(idea.corrected)
        self.assertTrue(idea.origin.startswith("ai:"))
        self.assertEqual(idea.confirmed_by, self.author)
        line = [l for l in self.act_lines() if "attribute" in l][0]
        self.assertIn(f"idea={idea.pk}", line)
        record = json.loads((self.path / ".sahalekhana/ideas.json").read_text())
        self.assertTrue(record["ideas"][0]["corrected"])

    def test_confirming_as_recorded_keeps_the_origin(self):
        idea = self._record(origin="me")
        chat.attribute(idea=idea, user=self.author, origin="keep")
        idea.refresh_from_db()
        self.assertFalse(idea.corrected)
        self.assertTrue(idea.origin.startswith("ai:"))
        self.assertIsNotNone(idea.confirmed_at)


class MethodTests(RepoTestCase):
    def _abstract(self):
        body = (r"\yukti{abstract}{uddesha}{Construing a verse first helps translation. "
                r"Case endings carry the relations. We test this on 200 verses.}")
        self.edit(r"\yukti{intro.thesis}{uddesha}{" + THESIS + "}", body, meaning=())
        return body

    def test_abstract_expansion_lays_out_one_step_per_sentence(self):
        self._abstract()
        run = methods.start(project=self.project, user=self.author, code="abstract_expansion", target="abstract")
        self.assertEqual(run.steps.count(), 3)
        source = self.src()
        self.assertIn(r"\yukti{abstract.s1}{nirdesha}{}", source)
        self.assertIn("% from the abstract, sentence 3:", source)
        self.assertTrue(any("method_start" in line for line in self.act_lines()))

    def test_a_step_counts_as_done_when_the_writer_has_written_it(self):
        self._abstract()
        run = methods.start(project=self.project, user=self.author, code="abstract_expansion", target="abstract")
        self.edit(r"\yukti{abstract.s1}{nirdesha}{}",
                  r"\yukti{abstract.s1}{nirdesha}{Verse order serves metre, not syntax.}")
        rows = methods.progress(run, self.src())
        self.assertTrue(rows[0]["written"] and rows[0]["done"])
        self.assertFalse(rows[1]["done"])

    def test_marking_a_step_done_is_an_act(self):
        self._abstract()
        run = methods.start(project=self.project, user=self.author, code="abstract_expansion", target="abstract")
        methods.mark(step=run.steps.first(), user=self.author, done=True)
        self.assertTrue(any("method_step" in line for line in self.act_lines()))

    def test_five_limbs_seed_placement_markers(self):
        run = methods.start(project=self.project, user=self.author, code="adhikarana_section", target="construe")
        self.assertEqual([s.unit_id for s in run.steps.all()][:2], ["construe.vishaya", "construe.samshaya"])
        self.assertIn(r"\ask{construe.purvapaksha}", self.src())

    def test_expanding_a_unit_that_does_not_exist_is_refused(self):
        with self.assertRaisesRegex(services.InvalidAction, "Write the abstract as a unit first"):
            methods.start(project=self.project, user=self.author, code="abstract_expansion", target="nope")
