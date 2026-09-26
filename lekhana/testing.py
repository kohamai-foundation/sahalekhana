"""Shared fixtures for the lekhana tests: a throwaway repository root, one
author, and one project, with the AI patched out unless a test patches it in."""
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from lekhana import ledger, services

PROPOSALS = [
    {"text": "Sorting a shuffled deck before dealing.",
     "check": "Relies on the verse having no order, which it does."},
    {"text": "A court interpreter settles who did what to whom before rendering the testimony.",
     "check": "Shared property: roles fixed before rendering. It holds for interpreters."},
    {"text": "Like unscrambling 50% of Yoda's lines & more [[owed: evidence that inverted order slows readers]].",
     "check": "The register may be wrong for the venue."},
]


class RepoTestCase(TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings_override = override_settings(LEKHANA_REPO_ROOT=tmp.name, ANTHROPIC_API_KEY="")
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.author = User.objects.create_user("ram", password="x", email="ram@example.org")
        self.project = services.create_project(user=self.author, title="Anvaya first", slug="anvaya-first")
        self.path = ledger.repo_path(self.project.slug)

    def src(self):
        return services.source(self.project)

    def last(self):
        return ledger.log(self.path)[0]

    def act_lines(self, entry=None):
        return [v for k, v in (entry or self.last())["trailers"] if k == "Act"]

    def propose(self, proposals=PROPOSALS, model="claude-opus-5"):
        return mock.patch.object(services.ai, "propose", return_value=(proposals, model))

    def ask(self, device="upamana", unit="intro.thesis", proposals=PROPOSALS):
        with self.propose(proposals):
            return services.ask(project=self.project, user=self.author, unit_id=unit, device=device,
                                count=3, instruction="Three analogies.", constraint="No pop culture.")

    def edit(self, old, new, meaning=("unit:intro.thesis",)):
        return services.save(project=self.project, user=self.author, content=self.src().replace(old, new),
                             base_sha=services.head(self.project), message="", meaning=set(meaning))

    def reviewer(self, username="sita"):
        user = User.objects.create_user(username, password="x")
        services.grant(project=self.project, user=self.author, username=username, role="reviewer")
        return user
