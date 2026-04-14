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


# ============================================================
# Stage detector — secondary actions + deep-linking
# ============================================================

class _JobStub:
    """Minimal stand-in for jobs.models.Job — only the fields the stage
    detector reads (id, company, created_at)."""
    def __init__(self, id, company=None, created_at='2026-04-14T10:00:00'):
        self.id = id
        self.company = company
        self.created_at = created_at


class CareerStageSecondaryActionsTests(SimpleTestCase):
    """The stage detector returns contextual secondary actions — verified here."""

    def test_offer_deep_links_to_specific_job_negotiator(self):
        job = _JobStub('abc-123', company='Stripe')
        s = detect_career_stage(
            has_profile=True,
            status_counts={'offer': 1},
            jobs_by_status={'offer': [job]},
        )
        self.assertEqual(s['key'], 'offer_in_hand')
        self.assertIn('abc-123', s['primary_href'])
        self.assertIn('salary', s['primary_href'])
        self.assertIn('Stripe', s['primary_label'])

    def test_interviewing_deep_links_to_specific_job_chatbot(self):
        job = _JobStub('iv-42', company='Airbnb')
        s = detect_career_stage(
            has_profile=True,
            status_counts={'interviewing': 1},
            jobs_by_status={'interviewing': [job]},
        )
        self.assertEqual(s['key'], 'interviewing')
        self.assertIn('iv-42', s['primary_href'])
        self.assertIn('chatbot', s['primary_href'])
        self.assertIn('Airbnb', s['primary_label'])

    def test_actively_applying_secondary_points_to_recent_job(self):
        job = _JobStub('app-99', company='Notion')
        s = detect_career_stage(
            has_profile=True,
            status_counts={'applied': 1},
            jobs_by_status={'applied': [job]},
        )
        labels = [a['label'] for a in s['secondary_actions']]
        # One of the secondary actions should reference the company name.
        self.assertTrue(
            any('Notion' in lbl for lbl in labels),
            f'expected a Notion-specific secondary action; got {labels}'
        )

    def test_all_stages_return_secondary_actions(self):
        """Every stage must return at least one secondary action, never an
        empty list — the dashboard template assumes the row renders."""
        stages = [
            detect_career_stage(has_profile=False, status_counts={}, jobs_by_status={}),
            detect_career_stage(has_profile=True,  status_counts={}, jobs_by_status={}),
            detect_career_stage(has_profile=True,  status_counts={'saved': 1},
                                jobs_by_status={'saved': [_JobStub('s1', 'X')]}),
            detect_career_stage(has_profile=True,  status_counts={'interviewing': 1},
                                jobs_by_status={'interviewing': [_JobStub('i1', 'Y')]}),
            detect_career_stage(has_profile=True,  status_counts={'offer': 1},
                                jobs_by_status={'offer': [_JobStub('o1', 'Z')]}),
            detect_career_stage(has_profile=True,  status_counts={'rejected': 1},
                                jobs_by_status={'rejected': [_JobStub('r1', 'W')]}),
        ]
        for s in stages:
            with self.subTest(key=s['key']):
                self.assertTrue(s['secondary_actions'], f"{s['key']} has no secondary actions")
                self.assertLessEqual(len(s['secondary_actions']), 3,
                                     f"{s['key']} has too many secondary actions")
                for a in s['secondary_actions']:
                    self.assertIn('label', a)
                    self.assertIn('href', a)

    def test_primary_href_falls_back_when_no_job_instance_supplied(self):
        """When only counts are supplied (legacy callers), interviewing/offer
        stages still pick a sensible generic URL."""
        s_iv = detect_career_stage(has_profile=True, status_counts={'interviewing': 1})
        self.assertTrue(s_iv['primary_href'])  # never blank
        s_off = detect_career_stage(has_profile=True, status_counts={'offer': 1})
        self.assertTrue(s_off['primary_href'])
