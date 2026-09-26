"""How the text arrived: counted, classified, committed — and never stored."""
import json

from django.urls import reverse

from lekhana import composition, services
from lekhana.models import CompositionEvent, Snapshot
from lekhana.testing import PROPOSALS, RepoTestCase

UA = {"HTTP_USER_AGENT": "Mozilla/5.0"}
THESIS = "State the paper's thesis in one sentence."


class ClassifyTests(RepoTestCase):
    def test_a_paste_of_an_ai_candidate_is_recognised_as_one(self):
        thread = self.ask()
        candidate = thread.candidates.get(index=2)
        made = composition.record(project=self.project, user=self.author, source_text=self.src(), events=[
            {"kind": "paste", "chars": len(candidate.text), "hash": composition.digest(candidate.text),
             "unit_id": "intro.thesis"}])
        self.assertEqual(made[0].source, CompositionEvent.Source.AI_CANDIDATE)
        self.assertEqual(made[0].matched_candidate, candidate)

    def test_a_paste_from_the_paper_and_from_outside_are_told_apart(self):
        made = composition.record(project=self.project, user=self.author, source_text=self.src(), events=[
            {"kind": "paste", "chars": 40, "hash": composition.digest(THESIS)},
            {"kind": "paste", "chars": 60, "hash": composition.digest("Something written elsewhere.")}])
        self.assertEqual([e.source for e in made],
                         [CompositionEvent.Source.OWN_PAPER, CompositionEvent.Source.OUTSIDE])

    def test_whitespace_differences_do_not_hide_a_match(self):
        self.assertEqual(composition.digest(" State   the paper's\nthesis in one sentence. "),
                         composition.digest(THESIS))

    def test_no_pasted_text_is_stored(self):
        made = composition.record(project=self.project, user=self.author, source_text=self.src(), events=[
            {"kind": "paste", "chars": 20, "hash": composition.digest("secret wording")}])
        stored = json.dumps([{f.name: str(getattr(made[0], f.name)) for f in CompositionEvent._meta.fields}])
        self.assertNotIn("secret wording", stored)

    def test_tracking_off_records_nothing(self):
        services.set_tracking(project=self.project, user=self.author, on=False)
        self.assertEqual(composition.record(project=self.project, user=self.author, source_text="",
                                            events=[{"kind": "typing", "chars": 10}]), [])
        self.assertTrue(any("set_tracking" in line for line in self.act_lines()))

    def test_malformed_events_are_dropped_not_guessed(self):
        made = composition.record(project=self.project, user=self.author, source_text=self.src(), events=[
            {"kind": "telepathy", "chars": 10}, {"kind": "typing", "chars": 0},
            {"kind": "typing", "chars": 12, "unit_id": "not a unit id"}])
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].unit_id, "")


class CommitTests(RepoTestCase):
    def test_a_commit_carries_the_composition_summary(self):
        composition.record(project=self.project, user=self.author, source_text=self.src(), events=[
            {"kind": "typing", "chars": 140, "unit_id": "intro.thesis"},
            {"kind": "paste", "chars": 60, "hash": composition.digest("From my notes."),
             "unit_id": "intro.thesis"}])
        self.edit(THESIS, "Construing a verse first helps its translation.")
        compose = [v for k, v in self.last()["trailers"] if k == "Compose"]
        self.assertEqual(compose, ["typed=140 pasted=60 pastes=1 outside=1"])
        self.assertFalse(composition.pending(self.project).exists())

    def test_per_unit_and_per_commit_summaries_read_back(self):
        composition.record(project=self.project, user=self.author, source_text=self.src(), events=[
            {"kind": "typing", "chars": 300, "unit_id": "intro.thesis"},
            {"kind": "paste", "chars": 100, "hash": "", "unit_id": "intro.thesis"}])
        self.edit(THESIS, "Rewritten.")
        unit = composition.for_unit(self.project, "intro.thesis")
        self.assertEqual((unit["typed_pct"], unit["pasted_pct"], unit["unknown"]), (75, 25, 1))
        self.assertEqual(composition.for_commit(self.project, services.head(self.project))["pastes"], 1)


class EndpointTests(RepoTestCase):
    def url(self):
        return reverse("lekhana:compose", args=[self.project.slug])

    def test_the_editor_can_report_and_snapshots_are_kept(self):
        self.client.force_login(self.author)
        response = self.client.post(self.url(), data=json.dumps({
            "events": [{"kind": "typing", "chars": 25}], "snapshot": "\\documentclass{article}\n"}),
            content_type="application/json", **UA)
        self.assertEqual(response.json()["recorded"], 1)
        self.assertEqual(Snapshot.objects.filter(project=self.project).count(), 1)

    def test_snapshots_are_pruned_and_unchanged_buffers_are_not_duplicated(self):
        for i in range(composition.SNAPSHOTS_KEPT + 4):
            composition.snapshot(project=self.project, user=self.author, content=f"draft {i}")
        composition.snapshot(project=self.project, user=self.author,
                             content=f"draft {composition.SNAPSHOTS_KEPT + 3}")
        self.assertEqual(Snapshot.objects.count(), composition.SNAPSHOTS_KEPT)

    def test_a_reviewer_cannot_report_composition(self):
        self.client.force_login(self.reviewer())
        response = self.client.post(self.url(), data=json.dumps({"events": []}),
                                    content_type="application/json", **UA)
        self.assertEqual(response.status_code, 403)

    def test_bad_json_is_refused(self):
        self.client.force_login(self.author)
        self.assertEqual(self.client.post(self.url(), data="not json",
                                          content_type="application/json", **UA).status_code, 400)
