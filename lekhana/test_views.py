from django.contrib.auth.models import User
from django.urls import reverse

from lekhana import services
from lekhana.models import Ask, Project
from lekhana.testing import RepoTestCase

UA = {"HTTP_USER_AGENT": "Mozilla/5.0"}


class PortalViewTests(RepoTestCase):
    def url(self, name, *args):
        return reverse(f"lekhana:{name}", args=[self.project.slug, *args])

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = self.client.get(reverse("lekhana:index"), **UA)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_someone_without_access_gets_404(self):
        self.client.force_login(User.objects.create_user("outsider", password="x"))
        self.assertEqual(self.client.get(self.url("project"), **UA).status_code, 404)
        self.assertEqual(self.client.get(self.url("review"), **UA).status_code, 404)

    def test_author_sees_the_editor(self):
        self.client.force_login(self.author)
        response = self.client.get(self.url("project"), **UA)
        self.assertContains(response, "intro.thesis")
        self.assertContains(response, "Review changes")

    def test_create_a_paper(self):
        self.client.force_login(self.author)
        response = self.client.post(reverse("lekhana:index"), {"title": "Second paper"}, **UA)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(slug="second-paper").exists())

    def test_review_then_commit(self):
        self.client.force_login(self.author)
        content = services.source(self.project).replace("State the paper's thesis in one sentence.", "It helps.")
        head = services.head(self.project)
        review = self.client.post(self.url("save"), {"content": content, "base_sha": head, "step": "review"}, **UA)
        self.assertContains(review, 'value="unit:intro.thesis"')
        commit = self.client.post(self.url("save"), {"content": content, "base_sha": head, "step": "commit",
                                                     "meaning": "unit:intro.thesis"}, **UA)
        self.assertEqual(commit.status_code, 302)
        self.assertNotEqual(services.head(self.project), head)

    def test_unparseable_source_is_kept_in_the_editor(self):
        self.client.force_login(self.author)
        response = self.client.post(self.url("save"), {"content": "\\yukti{a}{x}{open", "step": "review",
                                                       "base_sha": services.head(self.project)}, **UA)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "unclosed brace", status_code=400)
        self.assertContains(response, "\\yukti{a}{x}{open", status_code=400)

    def test_ask_without_ai_configured_is_recorded_and_explained(self):
        self.client.force_login(self.author)
        response = self.client.post(self.url("ask"), {"unit_id": "intro.thesis", "device": "upamana",
                                                      "count": "3", "instruction": "x"}, **UA, follow=True)
        self.assertContains(response, "ANTHROPIC_API_KEY")
        self.assertEqual(Ask.objects.get().status, Ask.Status.FAILED)

    def test_reviewer_is_sent_to_review_and_cannot_edit(self):
        sita = self.reviewer()
        self.client.force_login(sita)
        self.assertRedirects(self.client.get(self.url("project"), **UA), self.url("review"),
                             fetch_redirect_response=False)
        response = self.client.post(self.url("save"), {"content": "x", "step": "commit", "base_sha": ""}, **UA)
        self.assertEqual(response.status_code, 403)

    def test_review_page_shows_a_units_history(self):
        sita = self.reviewer()
        self.client.force_login(sita)
        response = self.client.get(self.url("review") + "?unit=intro.thesis", **UA)
        self.assertContains(response, "came to be")
        self.assertContains(response, "State the paper")

    def test_reviewer_can_record_a_point(self):
        sita = self.reviewer()
        self.client.force_login(sita)
        response = self.client.post(self.url("point"), {"unit_id": "intro.thesis", "kind": "question",
                                                        "text": "Which translations?"}, **UA)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project.review_points.get().author, sita)

    def test_ledger_lists_commits_and_access(self):
        self.client.force_login(self.author)
        response = self.client.get(self.url("ledger"), **UA)
        self.assertContains(response, "Frame the work")
        self.assertContains(response, "Contribution profile")
