"""Tests for core services — career stage detection and welcome flow.

Stage detection drives the dashboard hero (label, copy, primary CTA) so
the priority order matters: an offer always beats interviews, interviews
beat applying, applying beats just-looking, etc.

Welcome flow tests cover the first-run orchestration: newly-signed-up
users land on /welcome/; repeat visits short-circuit to the dashboard.
"""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.services.career_stage import detect_career_stage


class DetectCareerStageTests(SimpleTestCase):
    def test_no_profile_returns_getting_started(self):
        s = detect_career_stage(has_profile=False, status_counts={})
        self.assertEqual(s['key'], 'getting_started')
        self.assertEqual(s['primary_route'], 'upload_master_profile')

    def test_profile_no_jobs_returns_ready_to_look(self):
        s = detect_career_stage(has_profile=True, status_counts={})
        self.assertEqual(s['key'], 'ready_to_look')
        self.assertEqual(s['primary_route'], 'job_input_view')

    def test_only_saved_returns_actively_applying(self):
        s = detect_career_stage(has_profile=True, status_counts={'saved': 3})
        self.assertEqual(s['key'], 'actively_applying')

    def test_only_applied_returns_actively_applying(self):
        s = detect_career_stage(has_profile=True, status_counts={'applied': 5})
        self.assertEqual(s['key'], 'actively_applying')

    def test_interviewing_overrides_applying(self):
        s = detect_career_stage(has_profile=True, status_counts={
            'saved': 5, 'applied': 8, 'interviewing': 1,
        })
        self.assertEqual(s['key'], 'interviewing')
        self.assertEqual(s['tone'], 'accent')

    def test_offer_is_top_priority(self):
        # Even with active interviews and applications, an offer wins.
        s = detect_career_stage(has_profile=True, status_counts={
            'saved': 5, 'applied': 8, 'interviewing': 2, 'offer': 1,
        })
        self.assertEqual(s['key'], 'offer_in_hand')
        self.assertEqual(s['tone'], 'success')

    def test_only_rejected_returns_reflecting(self):
        s = detect_career_stage(has_profile=True, status_counts={'rejected': 3})
        self.assertEqual(s['key'], 'reflecting')

    def test_rejected_loses_to_interviewing(self):
        s = detect_career_stage(has_profile=True, status_counts={
            'rejected': 3, 'interviewing': 1,
        })
        self.assertEqual(s['key'], 'interviewing')

    def test_status_counts_are_case_insensitive(self):
        s = detect_career_stage(has_profile=True, status_counts={'OFFER': 1})
        self.assertEqual(s['key'], 'offer_in_hand')

    def test_missing_keys_treated_as_zero(self):
        s = detect_career_stage(has_profile=True, status_counts={'applied': 0})
        self.assertEqual(s['key'], 'ready_to_look')

    def test_each_stage_has_required_fields(self):
        # Every returned stage must have the fields the template relies on.
        for ctx in [
            (False, {}),
            (True, {}),
            (True, {'saved': 1}),
            (True, {'interviewing': 1}),
            (True, {'offer': 1}),
            (True, {'rejected': 1}),
        ]:
            with self.subTest(ctx=ctx):
                s = detect_career_stage(has_profile=ctx[0], status_counts=ctx[1])
                for required in ('key', 'label', 'detail', 'primary_label', 'primary_href', 'tone'):
                    self.assertIn(required, s)
                    self.assertTrue(s[required], f"empty value for {required}")


# ============================================================
# Welcome / onboarding orchestrator
# ============================================================

class WelcomeViewTests(TestCase):
    """Behavior of /welcome/ — the first-run orientation screen.

    These need a real DB (auth.User + UserProfile creation + data_content
    persistence) so they're TestCase, not SimpleTestCase.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='jane@example.com', email='jane@example.com', password='pw1234pw'
        )
        self.client.force_login(self.user)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse('welcome'))
        # login_required → 302 to login URL with next=/welcome/
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

    def test_fresh_signup_sees_welcome_once(self):
        # First visit: welcome screen renders.
        resp = self.client.get(reverse('welcome'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Meet your')

        # Flag is persisted — second visit skips.
        resp2 = self.client.get(reverse('welcome'))
        self.assertEqual(resp2.status_code, 302)
        self.assertTrue(resp2.url.endswith('/profiles/dashboard/'))

    def test_skip_posts_redirect_to_dashboard_and_mark_seen(self):
        resp = self.client.post(reverse('welcome'), {'action': 'skip'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith('/profiles/dashboard/'))

        # Subsequent GET short-circuits.
        resp2 = self.client.get(reverse('welcome'))
        self.assertEqual(resp2.status_code, 302)

    def test_user_with_existing_profile_content_bypasses_welcome(self):
        """A user who somehow has a profile already (e.g., re-signup, CV upload
        before hitting /welcome/) shouldn't get the welcome screen — the agent
        is past introduction."""
        from profiles.models import UserProfile
        UserProfile.objects.create(user=self.user, full_name='Jane Doe')
        resp = self.client.get(reverse('welcome'))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith('/profiles/dashboard/'))


class RegisterRedirectTests(TestCase):
    """Signup redirects to /welcome/ (not dashboard)."""

    def test_successful_registration_redirects_to_welcome(self):
        resp = self.client.post(reverse('register'), {
            'email': 'newbie@example.com',
            'password': 'pw1234pw',
            'confirm_password': 'pw1234pw',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith('/welcome/'))
