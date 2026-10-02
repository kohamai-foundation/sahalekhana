import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from lekhana import ai, grammar

CANDIDATES = {"candidates": [{"text": "A", "check": "a"}, {"text": "B", "check": "b"}, {"text": " ", "check": "x"}]}


def _response(*, content=None, refusal=None, finish_reason="stop", model="gpt-5.5", tool_calls=None):
    message = SimpleNamespace(content=json.dumps(CANDIDATES) if content is None else content,
                              refusal=refusal, tool_calls=tool_calls)
    return SimpleNamespace(model=model,
                           choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])


@override_settings(OPENAI_API_KEY="test-key", LEKHANA_MODEL="gpt-5.5")
class ProposeTests(SimpleTestCase):
    def _call(self, response, count=3, purpose="write"):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = response
        with mock.patch.object(ai, "_client", return_value=client):
            out = ai.propose(source="\\yukti{a}{uddesha}{x}", unit_id="a", unit_body="x",
                             device=grammar.DEVICE["upamana"], count=count, instruction="", constraint="",
                             purpose=purpose)
        return out, client.chat.completions.create.call_args.kwargs

    def test_returns_usable_candidates_and_requests_structured_output(self):
        (candidates, model), kwargs = self._call(_response())
        self.assertEqual([c["text"] for c in candidates], ["A", "B"])
        self.assertEqual(model, "gpt-5.5")
        self.assertEqual(kwargs["model"], "gpt-5.5")
        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertTrue(kwargs["response_format"]["json_schema"]["strict"])
        self.assertEqual(kwargs["messages"][0]["role"], "system")

    def test_the_answering_model_is_reported(self):
        (_, model), _ = self._call(_response(model="gpt-5.5-2026-04-01"))
        self.assertEqual(model, "gpt-5.5-2026-04-01")

    def test_count_caps_the_candidates(self):
        (candidates, _), _ = self._call(_response(), count=1)
        self.assertEqual(len(candidates), 1)

    def test_refusal_is_reported_with_what_the_model_said(self):
        with self.assertRaisesRegex(ai.AIRefused, r"declined this request \(No\.\)"):
            self._call(_response(content=None, refusal="No."))

    def test_a_cut_off_answer_is_a_failure(self):
        with self.assertRaisesRegex(ai.AIFailure, "cut off"):
            self._call(_response(finish_reason="length"))

    def test_unreadable_output_is_a_failure(self):
        with self.assertRaises(ai.AIFailure):
            self._call(_response(content="not json"))

    @override_settings(OPENAI_API_KEY="")
    def test_unconfigured_server_says_so(self):
        with self.assertRaisesRegex(ai.AIUnavailable, "OPENAI_API_KEY"):
            ai.propose(source="", unit_id="a", unit_body=None, device=grammar.DEVICE["upamana"],
                       count=1, instruction="", constraint="")

    def test_review_prompt_says_nothing_is_placed(self):
        prompt = ai.build_prompt(source="", unit_id="", unit_body=None, device=grammar.REVIEW_DEVICE["consistency"],
                                 count=1, instruction="", constraint="", purpose="review")
        self.assertIn("Nothing you return is placed in the paper", prompt)
        self.assertIn("about the whole paper", prompt)


@override_settings(OPENAI_API_KEY="test-key", LEKHANA_MODEL="gpt-5.5")
class CallTests(SimpleTestCase):
    """The seam: portal-shaped tools go in, a portal-shaped Reply comes out."""

    def _tool_call(self, name="read_paper", arguments='{"unit_id": "a"}'):
        return SimpleNamespace(id="tc1", function=SimpleNamespace(name=name, arguments=arguments))

    def test_portal_tools_are_translated_to_function_tools(self):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = _response(content="hello")
        ai.call(client, model="gpt-5.5", max_tokens=100, system="S", messages=[{"role": "user", "content": "hi"}],
                tools=[{"name": "read_paper", "description": "d", "strict": True,
                        "input_schema": {"type": "object", "properties": {}, "required": []}}])
        tool = client.chat.completions.create.call_args.kwargs["tools"][0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "read_paper")
        self.assertEqual(tool["function"]["parameters"]["type"], "object")
        self.assertTrue(tool["function"]["strict"])

    def test_tool_calls_are_parsed_and_the_turn_can_be_replayed(self):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = _response(
            content=None, finish_reason="tool_calls", tool_calls=[self._tool_call()])
        reply = ai.call(client, model="gpt-5.5", max_tokens=100, system="S", messages=[])
        self.assertEqual([(c.name, c.input) for c in reply.tool_calls], [("read_paper", {"unit_id": "a"})])
        self.assertEqual(reply.message["tool_calls"][0]["id"], "tc1")
        self.assertEqual(ai.tool_result(reply.tool_calls[0], "the source"),
                         {"role": "tool", "tool_call_id": "tc1", "content": "the source"})

    def test_unparseable_tool_arguments_do_not_crash_the_turn(self):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = _response(
            content=None, finish_reason="tool_calls", tool_calls=[self._tool_call(arguments="{oops")])
        reply = ai.call(client, model="gpt-5.5", max_tokens=100, system="S", messages=[])
        self.assertEqual(reply.tool_calls[0].input, {})

    def test_a_model_without_a_reasoning_budget_is_retried_without_the_knob(self):
        import openai
        client = mock.MagicMock()
        error = openai.BadRequestError(
            "Unsupported parameter: 'reasoning_effort'", response=mock.MagicMock(status_code=400), body=None)
        client.chat.completions.create.side_effect = [error, _response(content="hello")]
        reply = ai.call(client, model="gpt-4.1", max_tokens=100, system="S", messages=[])
        self.assertEqual(reply.text, "hello")
        self.assertNotIn("reasoning_effort", client.chat.completions.create.call_args.kwargs)

    def test_a_content_filter_reads_as_a_refusal(self):
        client = mock.MagicMock()
        client.chat.completions.create.return_value = _response(content="", finish_reason="content_filter")
        self.assertEqual(ai.call(client, model="gpt-5.5", max_tokens=100, system="S", messages=[]).refusal,
                         "content filtered")
