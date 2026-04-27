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
from profiles.models import OutreachAction, OutreachActionEvent, OutreachCampaign, UserProfile
from profiles.services.outreach_dispatcher import (
    MAX_ATTEMPTS,
    STALE_INFLIGHT_AFTER,
    claim_next_action,
    invites_sent_today,
    reclaim_stale_inflight,
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


class ActionEventLogTests(TestCase):
    """Every state transition on an OutreachAction should append an event
    row so we can answer 'why did this fail at 14:32?' or 'how often did
    this bounce queued ↔ failed?' without losing history on retry. Events
    are append-only (admin enforces read-only in OutreachActionEventAdmin).
    """

    def test_claim_logs_dispatch_event(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
        )
        claimed = claim_next_action(user)
        events = list(OutreachActionEvent.objects.filter(action=claimed))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.from_status, 'queued')
        self.assertEqual(ev.to_status, 'in_flight')
        self.assertEqual(ev.actor, 'server_dispatch')
        self.assertEqual(ev.attempts_after, 1)

    def test_record_action_result_logs_extension_event(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        record_action_result(action, 'sent')
        ev = OutreachActionEvent.objects.filter(action=action).first()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.from_status, 'in_flight')
        self.assertEqual(ev.to_status, 'sent')
        self.assertEqual(ev.actor, 'extension')

    def test_stale_recovery_logs_requeue_event(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        OutreachAction.objects.filter(pk=action.pk).update(
            queued_at=timezone.now() - STALE_INFLIGHT_AFTER - timedelta(minutes=1),
        )
        reclaim_stale_inflight(user)
        ev = OutreachActionEvent.objects.filter(action=action).first()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.from_status, 'in_flight')
        self.assertEqual(ev.to_status, 'queued')
        self.assertEqual(ev.actor, 'server_recovery')
        self.assertIn('stale_inflight', ev.reason)

    def test_full_lifecycle_produces_ordered_event_chain(self):
        """A canonical happy-path action: queued -> in_flight (claim) ->
        sent (extension reports success). The event log should show that
        sequence in order, with stable from/to fields."""
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
        )
        claimed = claim_next_action(user)
        record_action_result(claimed, 'sent')
        events = list(
            OutreachActionEvent.objects.filter(action=claimed).order_by('created_at')
        )
        self.assertEqual([(e.from_status, e.to_status) for e in events], [
            ('queued', 'in_flight'),
            ('in_flight', 'sent'),
        ])
        self.assertEqual([e.actor for e in events], ['server_dispatch', 'extension'])

    def test_event_log_failure_does_not_break_dispatch(self):
        """If the events table is somehow unavailable (corrupted index,
        transient outage), dispatch must still succeed. _log_event swallows
        and warns; verify that here by mocking the manager to raise."""
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
        )
        with patch.object(
            OutreachActionEvent.objects, 'create',
            side_effect=Exception('events table down'),
        ):
            # Should not raise — dispatch is the system of record, log is observability.
            claimed = claim_next_action(user)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, 'in_flight')


class StaleInflightRecoveryTests(TestCase):
    """A crashed extension can leave actions in `in_flight` indefinitely.
    `claim_next_action` only ever pulls `status='queued'`, so without the
    stale-recovery sweep the queue stalls. These tests pin the recovery
    behavior so it can't regress silently.
    """

    def test_stale_inflight_under_max_attempts_returns_to_queued(self):
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        # Force queued_at well past the staleness cutoff
        OutreachAction.objects.filter(pk=action.pk).update(
            queued_at=timezone.now() - STALE_INFLIGHT_AFTER - timedelta(minutes=1),
        )
        n = reclaim_stale_inflight(user)
        self.assertEqual(n, 1)
        action.refresh_from_db()
        self.assertEqual(action.status, 'queued')
        # Attempts is preserved — the cap should apply over the action's
        # lifetime, not reset on every reclaim.
        self.assertEqual(action.attempts, 1)
        self.assertIsNone(action.completed_at)

    def test_stale_inflight_at_max_attempts_marked_failed(self):
        """An action that has already burned all its attempts shouldn't
        loop queued -> in_flight -> stale -> queued forever. Move it to
        terminal `failed` so the campaign can finish."""
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', kind='connect', payload='hi',
            status='in_flight', attempts=MAX_ATTEMPTS,
        )
        OutreachAction.objects.filter(pk=action.pk).update(
            queued_at=timezone.now() - STALE_INFLIGHT_AFTER - timedelta(minutes=1),
        )
        n = reclaim_stale_inflight(user)
        self.assertEqual(n, 1)
        action.refresh_from_db()
        self.assertEqual(action.status, 'failed')
        self.assertIsNotNone(action.completed_at)
        self.assertIn('stale_inflight', action.last_error)

    def test_recent_inflight_is_not_reclaimed(self):
        """An action in_flight for less than the staleness window is
        legitimately mid-send (extension polling at 90s ± 20s, individual
        action takes ~8-15s) and must NOT be reclaimed."""
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        # queued_at just 1 minute ago (well within the 10-min window)
        OutreachAction.objects.filter(pk=action.pk).update(
            queued_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(reclaim_stale_inflight(user), 0)
        action.refresh_from_db()
        self.assertEqual(action.status, 'in_flight')

    def test_claim_next_action_recovers_stale_then_dispatches(self):
        """End-to-end: an extension crashed mid-action; the next poll comes
        in. claim_next_action sweeps the stale `in_flight` back to queued,
        then dispatches it (returns the same action with status flipped
        back to in_flight and attempts incremented)."""
        user = _make_user()
        job = _make_job(user)
        campaign = _make_campaign(user, job)
        action = OutreachAction.objects.create(
            campaign=campaign, target_handle='jane', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        OutreachAction.objects.filter(pk=action.pk).update(
            queued_at=timezone.now() - STALE_INFLIGHT_AFTER - timedelta(minutes=1),
        )
        claimed = claim_next_action(user)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, action.id)
        self.assertEqual(claimed.status, 'in_flight')
        # attempts went 1 (original) -> 1 (preserved during reclaim) -> 2 (claim bump)
        self.assertEqual(claimed.attempts, 2)

    def test_inflight_in_other_users_campaign_is_not_touched(self):
        """The recovery sweep is per-user. One user's stuck queue must not
        affect another user's actions."""
        user_a = _make_user(email='a@example.com')
        user_b = _make_user(email='b@example.com')
        job_b = _make_job(user_b)
        campaign_b = _make_campaign(user_b, job_b)
        action_b = OutreachAction.objects.create(
            campaign=campaign_b, target_handle='jane', kind='connect', payload='hi',
            status='in_flight', attempts=1,
        )
        OutreachAction.objects.filter(pk=action_b.pk).update(
            queued_at=timezone.now() - STALE_INFLIGHT_AFTER - timedelta(minutes=1),
        )
        # User A polls; should not touch user B's stale action.
        self.assertEqual(reclaim_stale_inflight(user_a), 0)
        action_b.refresh_from_db()
        self.assertEqual(action_b.status, 'in_flight')


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

    def test_retry_failed_endpoint_resets_actions_to_queued(self):
        """The /retry/ endpoint should flip every failed action in a campaign
        back to queued, clear last_error, reset attempts, and revive a
        campaign whose status had settled to failed/done."""
        self.client.force_login(self.user)
        campaign = _make_campaign(self.user, self.job, status='failed')
        a1 = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
            status='failed', attempts=3, last_error='timeout',
            completed_at=timezone.now(),
        )
        a2 = OutreachAction.objects.create(
            campaign=campaign, target_handle='b', kind='connect', payload='hi',
            status='failed', attempts=2, last_error='selector_drift:send',
            completed_at=timezone.now(),
        )
        # Sent action — must NOT be touched.
        sent = OutreachAction.objects.create(
            campaign=campaign, target_handle='c', kind='connect', payload='hi',
            status='sent', attempts=1, completed_at=timezone.now(),
        )
        url = f'/profiles/api/outreach/campaigns/{campaign.id}/retry/'
        res = self.client.post(url, data='{}', content_type='application/json')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['requeued'], 2)
        self.assertEqual(body['campaign_status'], 'running')
        a1.refresh_from_db(); a2.refresh_from_db(); sent.refresh_from_db()
        for a in (a1, a2):
            self.assertEqual(a.status, 'queued')
            self.assertEqual(a.attempts, 0)
            self.assertIsNone(a.completed_at)
            self.assertEqual(a.last_error, '')
        # Sent action untouched
        self.assertEqual(sent.status, 'sent')

    def test_retry_failed_endpoint_supports_action_ids_subset(self):
        """When body includes action_ids, only those failed actions are
        retried — useful for the "retry just this one" UI affordance."""
        self.client.force_login(self.user)
        campaign = _make_campaign(self.user, self.job)
        a1 = OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
            status='failed', attempts=3, last_error='x',
        )
        a2 = OutreachAction.objects.create(
            campaign=campaign, target_handle='b', kind='connect', payload='hi',
            status='failed', attempts=3, last_error='y',
        )
        url = f'/profiles/api/outreach/campaigns/{campaign.id}/retry/'
        res = self.client.post(
            url, data=json.dumps({'action_ids': [str(a1.id)]}),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['requeued'], 1)
        a1.refresh_from_db(); a2.refresh_from_db()
        self.assertEqual(a1.status, 'queued')
        self.assertEqual(a2.status, 'failed')  # not in subset

    def test_retry_failed_endpoint_requires_login(self):
        campaign = _make_campaign(self.user, self.job)
        url = f'/profiles/api/outreach/campaigns/{campaign.id}/retry/'
        res = self.client.post(url, data='{}', content_type='application/json')
        # Anonymous → redirects to login
        self.assertIn(res.status_code, (302, 401, 403))

    def test_retry_failed_endpoint_scoped_to_owner(self):
        """Another user must not be able to retry someone else's campaign."""
        other = _make_user(email='other@example.com')
        self.client.force_login(other)
        campaign = _make_campaign(self.user, self.job)
        OutreachAction.objects.create(
            campaign=campaign, target_handle='a', kind='connect', payload='hi',
            status='failed', attempts=3,
        )
        url = f'/profiles/api/outreach/campaigns/{campaign.id}/retry/'
        res = self.client.post(url, data='{}', content_type='application/json')
        self.assertEqual(res.status_code, 404)

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
