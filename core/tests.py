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


class AuthPagesRedirectWhenLoggedInTests(TestCase):
    """Authenticated users shouldn't see /login/ or /register/."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='loggedin@example.com', email='loggedin@example.com', password='pw1234pw',
        )
        self.client.force_login(self.user)

    def test_login_redirects_logged_in_user_to_dashboard(self):
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith('/profiles/dashboard/'))

    def test_register_redirects_logged_in_user_to_dashboard(self):
        resp = self.client.get(reverse('register'))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith('/profiles/dashboard/'))


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

    def test_interviewing_stage_includes_ask_agent_chip(self):
        job = _JobStub('deadbeef-dead-beef-dead-beefdeadbeef', company='Stripe')
        jobs_by_status = {'interviewing': [job]}
        s = detect_career_stage(
            has_profile=True,
            status_counts={'interviewing': 1},
            jobs_by_status=jobs_by_status,
        )
        labels = [a['label'] for a in s['secondary_actions']]
        hrefs = [a['href'] for a in s['secondary_actions']]
        self.assertTrue(any('Ask agent' in l for l in labels),
                        f"expected 'Ask agent' in {labels}")
        self.assertTrue(any(f"/agent/?job={job.id}" in h for h in hrefs),
                        f"expected /agent/?job= link in {hrefs}")


# ============================================================
# Agent chat (global) — system prompt assembly + chat dispatch
# ============================================================

from unittest.mock import MagicMock, patch


class BuildSystemPromptTests(TestCase):
    """build_system_prompt pulls context from the user's profile, signals,
    and applications. We verify each section shows up when relevant and is
    absent when empty."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='p@example.com', email='p@example.com', password='x'
        )

    def test_no_profile_yields_onboarding_guidance(self):
        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertIn('career agent', prompt)
        self.assertIn("hasn't built a profile yet", prompt)

    def test_profile_with_skills_and_experience_is_summarized(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user,
            full_name='Jane Doe',
            location='Cairo',
            data_content={
                'skills': [{'name': 'Python'}, {'name': 'PyTorch'}, 'SQL'],
                'experiences': [{'title': 'ML Eng', 'company': 'Stripe'}],
                'education': [{'degree': 'BSc CS', 'institution': 'KSIU'}],
            },
        )
        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertIn('Jane Doe', prompt)
        self.assertIn('Cairo', prompt)
        self.assertIn('Python', prompt)
        self.assertIn('PyTorch', prompt)
        self.assertIn('SQL', prompt)
        self.assertIn('ML Eng at Stripe', prompt)
        self.assertIn('BSc CS', prompt)

    def test_github_signals_render_when_present(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user,
            full_name='J',
            data_content={
                'github_signals': {
                    'username': 'janedoe',
                    'public_repos': 14,
                    'total_stars': 220,
                    'language_breakdown': [['Python', 8], ['TypeScript', 3]],
                },
            },
        )
        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertIn('GitHub @janedoe', prompt)
        self.assertIn('14 repos', prompt)
        self.assertIn('220 stars', prompt)
        self.assertIn('Python', prompt)

    def test_error_signals_are_skipped(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user,
            full_name='J',
            data_content={
                'github_signals': {'error': 'rate limited', 'username': 'x', 'public_repos': 99},
                'scholar_signals': {'error': 'captcha'},
            },
        )
        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertNotIn('GitHub @x', prompt)
        self.assertNotIn('Scholar', prompt)

    def test_application_pipeline_summary_is_included(self):
        from profiles.models import UserProfile
        from jobs.models import Job
        UserProfile.objects.create(user=self.user, full_name='J')
        Job.objects.create(user=self.user, title='A', company='X', application_status='applied')
        Job.objects.create(user=self.user, title='B', company='Y', application_status='interviewing')
        Job.objects.create(user=self.user, title='C', company='Z', application_status='applied')

        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertIn('Application pipeline', prompt)
        self.assertIn('2 applied', prompt)
        self.assertIn('1 interviewing', prompt)


class AgentChatApiTests(TestCase):
    """POST /agent/api/ — dispatches to the chat service with mocked LLM."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='c@example.com', email='c@example.com', password='x'
        )
        self.client.force_login(self.user)

    def test_get_is_rejected(self):
        resp = self.client.get(reverse('agent_chat_api'))
        self.assertEqual(resp.status_code, 405)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            reverse('agent_chat_api'), data='not json',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_message_returns_400(self):
        import json as _j
        resp = self.client.post(
            reverse('agent_chat_api'),
            data=_j.dumps({'history': [], 'message': '   '}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_happy_path_dispatches_to_llm_and_returns_reply(self):
        import json as _j
        fake_response = MagicMock(content='Here is a tight answer for you.')
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = fake_response
        with patch('core.services.agent_chat.get_llm', create=True, return_value=fake_llm), \
             patch('profiles.services.llm_engine.get_llm', return_value=fake_llm):
            resp = self.client.post(
                reverse('agent_chat_api'),
                data=_j.dumps({'history': [], 'message': 'What should I do next?'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['reply'], 'Here is a tight answer for you.')

    def test_llm_exception_returns_502_with_friendly_error(self):
        import json as _j
        fake_llm = MagicMock()
        fake_llm.invoke.side_effect = RuntimeError('network down')
        with patch('profiles.services.llm_engine.get_llm', return_value=fake_llm):
            resp = self.client.post(
                reverse('agent_chat_api'),
                data=_j.dumps({'history': [], 'message': 'Help me.'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 502)
        self.assertIn('agent', resp.json()['error'].lower())


class JobContextBlockTests(TestCase):
    """_build_job_context_block — renders a rich dossier for a single job."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='j@example.com', email='j@example.com', password='x'
        )

    def _make_job(self, **kwargs):
        from jobs.models import Job
        defaults = dict(
            user=self.user,
            title='Senior SWE',
            company='Stripe',
            description='Build scalable payment infra.',
            extracted_skills=['Python', 'Go', 'Kubernetes'],
            application_status='interviewing',
        )
        defaults.update(kwargs)
        return Job.objects.create(**defaults)

    def test_header_includes_title_company_status_and_skills(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertIn('Senior SWE', block)
        self.assertIn('Stripe', block)
        self.assertIn('interviewing', block)
        self.assertIn('Python', block)
        self.assertIn('Go', block)
        self.assertIn('Kubernetes', block)

    def test_includes_gap_analysis_when_present(self):
        from analysis.models import GapAnalysis
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        GapAnalysis.objects.create(
            job=job, user=self.user,
            matched_skills=['Python'],
            partial_skills=['Go'],
            missing_skills=['Kubernetes'],
            similarity_score=0.67,
        )
        block = _build_job_context_block(job)
        self.assertIn('Gap analysis', block)
        self.assertIn('67%', block)
        self.assertIn('Matched: Python', block)
        self.assertIn('Partial: Go', block)
        self.assertIn('Missing: Kubernetes', block)

    def test_omits_gap_section_when_no_analysis_cached(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertNotIn('Gap analysis', block)

    def test_includes_snapshot_note_when_present(self):
        from profiles.models import UserProfile, JobProfileSnapshot
        from core.services.agent_chat import _build_job_context_block
        profile = UserProfile.objects.create(
            user=self.user, full_name='J',
            data_content={'summary': 'Original', 'skills': [{'name': 'Python'}]},
        )
        job = self._make_job()
        JobProfileSnapshot.objects.create(
            profile=profile, job=job,
            data_content={'summary': 'Tailored for Stripe', 'skills': [{'name': 'Python'}, {'name': 'Go'}]},
            pre_chatbot_data={'summary': 'Original', 'skills': [{'name': 'Python'}]},
        )
        block = _build_job_context_block(job)
        self.assertIn('Job-specific profile variant', block)
        self.assertIn('summary', block)
        self.assertIn('skills', block)

    def test_omits_snapshot_section_when_absent(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertNotIn('Job-specific profile variant', block)

    def test_includes_artifacts_when_resume_and_cover_letter_exist(self):
        from analysis.models import GapAnalysis
        from resumes.models import GeneratedResume, CoverLetter
        from profiles.models import UserProfile
        from core.services.agent_chat import _build_job_context_block
        profile = UserProfile.objects.create(user=self.user, full_name='J')
        job = self._make_job()
        gap = GapAnalysis.objects.create(job=job, user=self.user, similarity_score=0.5)
        GeneratedResume.objects.create(gap_analysis=gap, name='v1', content={})
        CoverLetter.objects.create(job=job, profile=profile, content='Dear Stripe, ...')
        block = _build_job_context_block(job)
        self.assertIn('Artifacts for this job', block)
        self.assertIn('Tailored resume: yes', block)
        self.assertIn('Cover letter: yes', block)

    def test_omits_artifacts_section_when_none_exist(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertNotIn('Artifacts for this job', block)


class BuildSystemPromptWithJobTests(TestCase):
    """build_system_prompt gains an optional job parameter."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        self.user = get_user_model().objects.create_user(
            username='bsp@example.com', email='bsp@example.com', password='x'
        )
        UserProfile.objects.create(
            user=self.user, full_name='Jane',
            data_content={'skills': [{'name': 'Python'}]},
        )

    def test_prompt_without_job_omits_job_context_section(self):
        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertNotIn('JOB CONTEXT', prompt)

    def test_prompt_with_job_includes_job_context_section(self):
        from jobs.models import Job
        from core.services.agent_chat import build_system_prompt
        job = Job.objects.create(
            user=self.user, title='ML Eng', company='Stripe',
            description='x', extracted_skills=['Python'],
            application_status='interviewing',
        )
        prompt = build_system_prompt(self.user, job=job)
        self.assertIn('JOB CONTEXT', prompt)
        self.assertIn('ML Eng', prompt)
        self.assertIn('Stripe', prompt)


class AgentChatViewJobTests(TestCase):
    """GET /agent/?job=<id> — validates ownership, injects job into template."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='av@example.com', email='av@example.com', password='x'
        )
        self.other = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='x'
        )
        self.client.force_login(self.user)

    def _make_job(self, user, company='Stripe'):
        from jobs.models import Job
        return Job.objects.create(
            user=user, title='SWE', company=company,
            description='x', extracted_skills=['Python'],
            application_status='interviewing',
        )

    def test_no_job_param_renders_general_chat(self):
        resp = self.client.get(reverse('agent_chat'))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context.get('job'))

    def test_valid_owned_job_id_passes_job_to_template(self):
        job = self._make_job(self.user)
        resp = self.client.get(reverse('agent_chat') + f'?job={job.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context.get('job').id, job.id)
        self.assertEqual(str(resp.context.get('job_id')), str(job.id))

    def test_foreign_job_id_redirects_to_agent(self):
        foreign_job = self._make_job(self.other)
        resp = self.client.get(reverse('agent_chat') + f'?job={foreign_job.id}')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('agent_chat'))

    def test_invalid_uuid_redirects_to_agent(self):
        resp = self.client.get(reverse('agent_chat') + '?job=not-a-uuid')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('agent_chat'))

    def test_scope_pill_absent_in_general_chat(self):
        resp = self.client.get(reverse('agent_chat'))
        self.assertNotContains(resp, 'Talking about:')

    def test_scope_pill_renders_for_owned_job(self):
        job = self._make_job(self.user, company='Stripe')
        resp = self.client.get(reverse('agent_chat') + f'?job={job.id}')
        self.assertContains(resp, 'Talking about:')
        self.assertContains(resp, 'Stripe')
        # A dismiss link returning to general chat.
        self.assertContains(resp, 'href="' + reverse('agent_chat') + '"')

    def test_job_scoped_template_includes_jobId_in_alpine_state(self):
        job = self._make_job(self.user)
        resp = self.client.get(reverse('agent_chat') + f'?job={job.id}')
        # The template seeds Alpine with the job id for POST bodies.
        self.assertContains(resp, f"jobId: '{job.id}'")


class AgentChatApiJobTests(TestCase):
    """POST /agent/api/ with job_id — validates ownership, forwards job to chat()."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='api@example.com', email='api@example.com', password='x'
        )
        self.other = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='x'
        )
        self.client.force_login(self.user)

    def _make_job(self, user, company='Stripe'):
        from jobs.models import Job
        return Job.objects.create(
            user=user, title='SWE', company=company,
            description='x', extracted_skills=['Python'],
            application_status='interviewing',
        )

    def _post(self, body):
        import json as _j
        return self.client.post(
            reverse('agent_chat_api'),
            data=_j.dumps(body),
            content_type='application/json',
        )

    def test_valid_job_id_forwards_job_to_chat(self):
        from unittest.mock import patch, MagicMock
        job = self._make_job(self.user)
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content='scoped reply')
        with patch('profiles.services.llm_engine.get_llm', return_value=fake_llm), \
             patch('core.views.chat', wraps=__import__('core.services.agent_chat', fromlist=['chat']).chat) as spy:
            resp = self._post({'history': [], 'message': 'Prep me.', 'job_id': str(job.id)})
        self.assertEqual(resp.status_code, 200)
        kwargs = spy.call_args.kwargs if spy.call_args else {}
        self.assertIsNotNone(kwargs.get('job'))
        self.assertEqual(kwargs['job'].id, job.id)

    def test_foreign_job_id_returns_403(self):
        foreign = self._make_job(self.other)
        resp = self._post({'history': [], 'message': 'Hi', 'job_id': str(foreign.id)})
        self.assertEqual(resp.status_code, 403)
        self.assertIn('error', resp.json())

    def test_invalid_job_id_returns_403(self):
        resp = self._post({'history': [], 'message': 'Hi', 'job_id': 'not-a-uuid'})
        self.assertEqual(resp.status_code, 403)

    def test_missing_job_id_is_backwards_compatible(self):
        from unittest.mock import patch, MagicMock
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content='general reply')
        with patch('profiles.services.llm_engine.get_llm', return_value=fake_llm):
            resp = self._post({'history': [], 'message': 'Hi'})
        self.assertEqual(resp.status_code, 200)


class InsightsViewProfileStrengthTests(TestCase):
    """/insights/ includes profile_strength in its template context."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='iv@example.com', email='iv@example.com', password='x'
        )
        self.client.force_login(self.user)

    def test_insights_view_injects_profile_strength(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(user=self.user, full_name='J', email='j@e.com')
        resp = self.client.get(reverse('insights'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('profile_strength', resp.context)
        ps = resp.context['profile_strength']
        self.assertIn('score', ps)
        self.assertIn('top_actions', ps)

    def test_insights_renders_profile_strength_breakdown(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user, full_name='J', email='j@e.com',
            data_content={'skills': [{'name': s} for s in ['A', 'B', 'C', 'D', 'E']]},
        )
        resp = self.client.get(reverse('insights'))
        # Anchor for hash deep-link from the dashboard ring
        self.assertContains(resp, 'id="profile-strength"')
        # All three component labels render
        self.assertContains(resp, 'Completeness')
        self.assertContains(resp, 'Evidence depth')
        self.assertContains(resp, 'External signals')


class CsrfFailureViewTests(TestCase):
    """CSRF failures must render our styled 403 page, not Django's dev default.

    Can't rely on the default test Client — it bypasses CSRF. Use
    enforce_csrf_checks=True and POST without a token to a form endpoint.
    """

    def test_missing_csrf_token_renders_styled_page(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        resp = strict.post('/accounts/login/', {
            'email': 'x@example.com', 'password': 'y',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, 'session timed out', status_code=403)
        self.assertContains(resp, 'Back to the form', status_code=403)
        # Confirm we are NOT serving Django's built-in dev-mode CSRF page.
        self.assertNotContains(
            resp, 'CSRF verification failed', status_code=403,
        )

    def test_csrf_view_is_wired_in_settings(self):
        from django.conf import settings
        self.assertEqual(
            settings.CSRF_FAILURE_VIEW, 'core.views.csrf_failure',
        )


class OnboardingSkipFlowTests(TestCase):
    """Freshly-signed-up users see a "Skip onboarding" button on every step
    after /welcome/ (upload, review, job input). Existing users — who log
    in directly without ever visiting /welcome/ — do not.

    The flag lives in request.session['in_onboarding'], set by welcome_view
    when the chooser is rendered and cleared by skip_onboarding_view or on
    natural arrival at /profiles/dashboard/.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='onb@example.com', email='onb@example.com', password='x',
        )

    def test_welcome_view_sets_in_onboarding_flag(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        resp = self.client.get(reverse('welcome'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.client.session.get('in_onboarding'))

    def test_skip_shows_on_onboarding_pages_when_flag_set(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self.client.get(reverse('welcome'))  # sets the flag

        for url_name in ('upload_master_profile', 'review_master_profile', 'job_input_view'):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 200, url_name)
            self.assertContains(resp, 'Skip onboarding', msg_prefix=url_name)
            self.assertContains(
                resp, reverse('skip_onboarding'), msg_prefix=url_name,
            )

    def test_skip_hidden_when_flag_not_set(self):
        """Existing user logs in directly, never visits /welcome/ — no skip."""
        from django.urls import reverse
        self.client.force_login(self.user)
        for url_name in ('upload_master_profile', 'review_master_profile', 'job_input_view'):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 200, url_name)
            self.assertNotContains(resp, 'Skip onboarding', msg_prefix=url_name)

    def test_skip_endpoint_clears_flag_and_redirects(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self.client.get(reverse('welcome'))
        self.assertTrue(self.client.session.get('in_onboarding'))

        resp = self.client.post(reverse('skip_onboarding'))
        self.assertRedirects(resp, reverse('dashboard'))
        self.assertFalse(self.client.session.get('in_onboarding'))

    def test_skip_endpoint_rejects_get(self):
        """Guard against prefetchers / link previews triggering the skip."""
        from django.urls import reverse
        self.client.force_login(self.user)
        self.client.get(reverse('welcome'))
        self.assertTrue(self.client.session.get('in_onboarding'))

        resp = self.client.get(reverse('skip_onboarding'))
        # Don't fetch the target — dashboard clears the flag as a side effect.
        self.assertRedirects(
            resp, reverse('dashboard'), fetch_redirect_response=False,
        )
        # GET to /skip-onboarding/ itself must NOT clear the flag.
        self.assertTrue(self.client.session.get('in_onboarding'))

    def test_dashboard_naturally_clears_flag(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self.client.get(reverse('welcome'))
        self.assertTrue(self.client.session.get('in_onboarding'))

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.client.session.get('in_onboarding'))


class MessageAutoDismissTests(TestCase):
    """Success toasts auto-dismiss after 2s; errors/warnings stick until
    the user clicks the X. We can't run JS in these tests, but we can pin
    that the x-init timer is attached to the right message tags.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='msg@example.com', email='msg@example.com', password='oldpass1234',
        )

    def test_success_message_carries_2s_auto_dismiss_timer(self):
        """Trigger a genuine success flash by updating the password in
        /accounts/settings/, then follow the redirect and assert the
        rendered toast has the setTimeout wired in."""
        from django.urls import reverse
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('account_settings'),
            {
                'action': 'change_password',
                'current_password': 'oldpass1234',
                'new_password': 'newpass9876',
                'confirm_new_password': 'newpass9876',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Password updated successfully.')
        # Django does NOT html-escape arrow syntax inside attribute values;
        # the 2000ms timer must land in the DOM verbatim.
        self.assertContains(resp, 'setTimeout(() => show = false, 2000)')
        # And the success toast must not be rendered twice (settings.html used
        # to have its own messages block, duplicating the toast).
        self.assertEqual(
            resp.content.count(b'Password updated successfully.'), 1,
        )

    def test_no_multiline_django_comments_leak_into_rendered_html(self):
        """Django's {# #} is single-line only; a comment that wraps to the
        next line renders as literal text. Scan the post-password-update
        page for the {# prefix to catch any regressions in templates we
        hit during the auto-dismiss flow.
        """
        from django.urls import reverse
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('account_settings'),
            {
                'action': 'change_password',
                'current_password': 'oldpass1234',
                'new_password': 'newpass9876',
                'confirm_new_password': 'newpass9876',
            },
            follow=True,
        )
        self.assertNotIn(b'{#', resp.content)

    def test_error_message_has_no_auto_dismiss(self):
        """Error-tagged toasts must stick around so the user can read them."""
        from django.urls import reverse
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('account_settings'),
            {
                'action': 'change_password',
                'current_password': 'wrong-pw',
                'new_password': 'newpass9876',
                'confirm_new_password': 'newpass9876',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Current password is incorrect.')
        # The error toast's own alert div must NOT carry the timer.
        import re
        alerts = re.findall(
            r'<div[^>]*role="alert"[^>]*>.*?Current password is incorrect\.',
            resp.content.decode('utf-8'),
            re.DOTALL,
        )
        self.assertTrue(alerts, 'error toast not found')
        self.assertNotIn('setTimeout', alerts[0])
