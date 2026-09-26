import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from lekhana import ai, grammar

CANDIDATES = {"candidates": [{"text": "A", "check": "a"}, {"text": "B", "check": "b"}, {"text": " ", "check": "x"}]}


def _response(**overrides):
    fields = dict(stop_reason="end_turn", stop_details=None, model="claude-opus-5",
                  content=[SimpleNamespace(type="text", text=json.dumps(CANDIDATES))])
    fields.update(overrides)
    return SimpleNamespace(**fields)


@override_settings(ANTHROPIC_API_KEY="test-key", LEKHANA_MODEL="claude-opus-5")
class ProposeTests(SimpleTestCase):
    def _call(self, response, count=3, purpose="write"):
        client = mock.MagicMock()
        client.beta.messages.create.return_value = response
        with mock.patch.object(ai, "_client", return_value=client):
            out = ai.propose(source="\\yukti{a}{uddesha}{x}", unit_id="a", unit_body="x",
                             device=grammar.DEVICE["upamana"], count=count, instruction="", constraint="",
                             purpose=purpose)
        return out, client.beta.messages.create.call_args.kwargs

    def test_returns_usable_candidates_and_requests_fallbacks_and_structured_output(self):
        (candidates, model), kwargs = self._call(_response())
        self.assertEqual([c["text"] for c in candidates], ["A", "B"])
        self.assertEqual(model, "claude-opus-5")
        self.assertEqual(kwargs["model"], "claude-opus-5")
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["betas"], [ai.FALLBACK_BETA])
        self.assertEqual(kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")

    def test_the_answering_model_is_reported_when_a_fallback_ran(self):
        (_, model), _ = self._call(_response(model="claude-opus-4-8"))
        self.assertEqual(model, "claude-opus-4-8")

    def test_count_caps_the_candidates(self):
        (candidates, _), _ = self._call(_response(), count=1)
        self.assertEqual(len(candidates), 1)

    def test_refusal_is_reported_with_its_category(self):
        with self.assertRaisesRegex(ai.AIRefused, r"declined this request \(cyber\)"):
            self._call(_response(stop_reason="refusal", content=[], stop_details=SimpleNamespace(category="cyber")))

    def test_unreadable_output_is_a_failure(self):
        with self.assertRaises(ai.AIFailure):
            self._call(_response(content=[SimpleNamespace(type="text", text="not json")]))

    @override_settings(ANTHROPIC_API_KEY="")
    def test_unconfigured_server_says_so(self):
        with self.assertRaisesRegex(ai.AIUnavailable, "ANTHROPIC_API_KEY"):
            ai.propose(source="", unit_id="a", unit_body=None, device=grammar.DEVICE["upamana"],
                       count=1, instruction="", constraint="")

    def test_review_prompt_says_nothing_is_placed(self):
        prompt = ai.build_prompt(source="", unit_id="", unit_body=None, device=grammar.REVIEW_DEVICE["consistency"],
                                 count=1, instruction="", constraint="", purpose="review")
        self.assertIn("Nothing you return is placed in the paper", prompt)
        self.assertIn("about the whole paper", prompt)
