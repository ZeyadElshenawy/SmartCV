"""Tests for the outreach automation feature.

Covers:
  * model unique constraint and daily-cap query
  * people_finder URL/handle helpers and SERP soft-fail behavior
  * per-target drafting with the LLM mocked out
  * /api/outreach/* token auth + state transitions
"""
import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job
from profiles.models import OutreachAction, OutreachCampaign, UserProfile
from profiles.services.outreach_dispatcher import (
    claim_next_action,
    invites_sent_today,
    record_action_result,
)

User = get_user_model()


def _make_user(email='u@example.com', password='secret123!'):
    user = User.objects.create_user(username=email, email=email, password=password)
    UserProfile.objects.create(
        user=user, full_name='Test User', email=email, data_content={'summary': ''}
    )
    return user


def _make_job(user, title='Senior Engineer', company='Acme'):
    return Job.objects.create(
        user=user, title=title, company=company, description='desc', extracted_skills=[]
    )


def _make_campaign(user, job, status='running', cap=15):
    return OutreachCampaign.objects.create(
        user=user, job=job, status=status, daily_invite_cap=cap,
    )


class OutreachActionUniqueConstraintTests(TestCase):
    def test_duplicate_target_same_kind_rejected(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', kind='connect', payload='hi',
        )
        with self.assertRaises(IntegrityError):
            OutreachAction.objects.create(
                campaign=campaign, target_handle='jane', kind='connect', payload='dup',
            )


class DispatcherTests(TestCase):
    def test_claim_returns_oldest_queued_and_marks_in_flight(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        first = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='msg',
        )
        # Force second action to be created later for ordering predictability
        second = OutreachAction.objects.create(
            campaign=campaign, target_handle='b', kind='connect', payload='msg',
        )
        OutreachAction.objects.filter(pk=first.pk).update(
            queued_at=timezone.now() - timedelta(minutes=5),
        )

        claimed = claim_next_action(user)
        self.assertEqual(claimed.id, first.id)
        first.refresh_from_db()
        self.assertEqual(first.status, 'in_flight')
        self.assertEqual(first.attempts, 1)

    def test_claim_skips_when_daily_cap_hit(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job, cap=1)
        OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='msg',
            status='sent', completed_at=timezone.now(),
        )
        OutreachAction.objects.create(
            campaign=campaign, target_handle='b', kind='connect', payload='msg',
        )
        self.assertEqual(invites_sent_today(user), 1)
        self.assertIsNone(claim_next_action(user))

    def test_record_action_result_marks_complete_and_finishes_campaign(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='msg',
            status='in_flight', attempts=1,
        )
        record_action_result(action, 'sent')
        action.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(action.status, 'sent')
        self.assertIsNotNone(action.completed_at)
        self.assertEqual(campaign.status, 'done')

    def test_record_action_result_rejects_unknown_status(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='msg',
        )
        with self.assertRaises(ValueError):
            record_action_result(action, 'whatever')


class PeopleFinderTests(TestCase):
    def test_extract_handle_from_in_url(self):
        from jobs.services.people_finder import _extract_handle
        self.assertEqual(_extract_handle('https://www.linkedin.com/in/janedoe/'), 'janedoe')
        self.assertEqual(_extract_handle('https://linkedin.com/in/JaneDoe?utm=x'), 'janedoe')
        self.assertIsNone(_extract_handle(''))
        self.assertIsNone(_extract_handle('https://example.com/jane'))

    def test_find_peers_via_google_returns_empty_on_http_error(self):
        import requests
        from jobs.services.people_finder import find_peers_via_google
        with patch('jobs.services.people_finder.requests.get', side_effect=requests.RequestException('429')):
            self.assertEqual(find_peers_via_google('Acme', ['engineer']), [])

    def test_google_search_url_helper_escapes_query(self):
        from jobs.services.people_finder import google_search_url
        url = google_search_url('Acme & Co', ['senior engineer'])
        self.assertIn('linkedin.com', url)
        self.assertIn('Acme', url)


class PerTargetDraftingTests(TestCase):
    def test_returns_connect_message_capped_at_300(self):
        user = _make_user()
        job = _make_job(user)
        profile = user.profile
        target = MagicMock(name='Jane', role='Engineer', handle='jane')
        target.name = 'Jane'
        target.role = 'Engineer'

        fake_llm = MagicMock()
        fake_result = MagicMock()
        fake_result.model_dump.return_value = {
            'linkedin_message': 'x' * 500,
            'cold_email_subject': 'Hi',
            'cold_email_body': 'body',
        }
        fake_llm.invoke.return_value = fake_result
        with patch('profiles.services.outreach_generator.get_structured_llm', return_value=fake_llm):
            from profiles.services.outreach_generator import generate_outreach_for_target
            out = generate_outreach_for_target(profile, job, target)
        self.assertEqual(len(out['connect_message']), 300)
        self.assertEqual(out['follow_up_message'], 'body')


class OutreachApiTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.user.rotate_outreach_token()
        self.job = _make_job(self.user)
        self.client = Client()

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.user.outreach_token}'}

    def test_next_returns_401_without_token(self):
        res = self.client.get('/profiles/api/outreach/next')
        self.assertEqual(res.status_code, 401)

    def test_next_returns_204_when_nothing_queued(self):
        res = self.client.get('/profiles/api/outreach/next', **self._auth())
        self.assertEqual(res.status_code, 204)

    def test_next_returns_action_payload_when_queued(self):
        campaign = _make_campaign(self.user, self.job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', target_name='Jane Doe',
            kind='connect', payload='hi jane',
        )
        res = self.client.get('/profiles/api/outreach/next', **self._auth())
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['id'], str(action.id))
        self.assertEqual(body['target_handle'], 'jane')
        self.assertIn('https://www.linkedin.com/in/jane', body['profile_url'])
        action.refresh_from_db()
        self.assertEqual(action.status, 'in_flight')

    def test_result_endpoint_marks_action_sent(self):
        campaign = _make_campaign(self.user, self.job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        url = f'/profiles/api/outreach/result/{action.id}/'
        res = self.client.post(
            url, data=json.dumps({'status': 'sent'}), content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(res.status_code, 200)
        action.refresh_from_db()
        self.assertEqual(action.status, 'sent')

    def test_create_campaign_requires_login(self):
        res = self.client.post('/profiles/api/outreach/campaigns/',
                               data=json.dumps({'job_id': str(self.job.id), 'targets': []}),
                               content_type='application/json')
        self.assertIn(res.status_code, (302, 403))

    def test_create_campaign_caps_payload_at_300(self):
        self.client.force_login(self.user)
        res = self.client.post(
            '/profiles/api/outreach/campaigns/',
            data=json.dumps({
                'job_id': str(self.job.id),
                'daily_invite_cap': 10,
                'targets': [{'handle': 'jane', 'message': 'x' * 500, 'name': 'Jane'}],
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        action = OutreachAction.objects.get(target_handle='jane')
        self.assertEqual(len(action.payload), 300)
