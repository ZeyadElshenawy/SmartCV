"""Server-side dispatch helpers for the outreach automation feature.

The Chrome extension polls /api/outreach/next; this module decides what (if
anything) to hand back, and applies the per-user daily-invite cap so the cap
holds even if the extension is misbehaving.
"""

import logging
from datetime import timedelta
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from profiles.models import OutreachAction, OutreachActionEvent, OutreachCampaign

logger = logging.getLogger(__name__)

# An action that's been `in_flight` longer than this is presumed orphaned —
# the extension that claimed it crashed, the browser tab was closed, or the
# user revoked the token mid-action. We revert it to `queued` so the next
# poll can re-claim. The 10-minute window is generous: a normal connect
# flow takes ~8-15s including humanized delays, plus retries; anything
# beyond 10 minutes is almost certainly a hang.
STALE_INFLIGHT_AFTER = timedelta(minutes=10)

# Each action gets at most this many attempts before it's considered
# dead-on-arrival and never re-tried (matches record_action_result()).
MAX_ATTEMPTS = 3


def invites_sent_today(user) -> int:
    """Count completed connect actions for this user in the last 24h.

    Hard-stops dispatch when this hits the per-campaign daily_invite_cap.
    Counts in the rolling 24h window (not the calendar day) so a user that
    burned the cap at 23:00 cannot get another full batch at 00:00.
    """
    cutoff = timezone.now() - timedelta(hours=24)
    return OutreachAction.objects.filter(
        campaign__user=user,
        kind='connect',
        completed_at__gte=cutoff,
        status__in=['sent', 'accepted'],
    ).count()


def _log_event(action: OutreachAction, *, from_status: str, to_status: str,
               actor: str, reason: str = '', detail: str = '') -> None:
    """Append an audit-trail row for a state transition.

    Failures here MUST NOT break dispatch — the event log is observability,
    not the system of record. We swallow exceptions and warn so a missing
    table or a transient DB hiccup doesn't take down the queue.
    """
    try:
        OutreachActionEvent.objects.create(
            action=action,
            from_status=from_status or '',
            to_status=to_status,
            actor=actor,
            reason=reason or '',
            detail=detail or '',
            attempts_after=action.attempts,
        )
    except Exception as exc:  # noqa: BLE001 — never let logging break dispatch
        logger.warning("outreach: event log write failed (action=%s): %s", action.id, exc)


def reclaim_stale_inflight(user) -> int:
    """Revert orphaned `in_flight` actions back to `queued`.

    An action gets stuck in `in_flight` if the extension that claimed it
    crashed, the browser tab was closed, the network died mid-send, or the
    user revoked their outreach token between claim and report. Without
    this sweep, those actions sit `in_flight` forever and the queue
    stalls — `claim_next_action` only ever pulls `status='queued'`.

    We give the extension a generous window (`STALE_INFLIGHT_AFTER`) to
    actually finish the action before declaring it stale, so a slow
    legitimate flow isn't reverted out from under itself.

    Actions that have already burned `MAX_ATTEMPTS` are NOT reclaimed —
    they're moved to `failed` with `completed_at` set so the campaign can
    finish cleanly. Otherwise a permanently-failing action would loop
    queued -> in_flight -> stale -> queued forever.

    Returns the number of actions touched (for telemetry / tests).
    """
    # Fast bail: no stale rows means we don't need to materialize anything.
    cutoff = timezone.now() - STALE_INFLIGHT_AFTER
    stuck_qs = OutreachAction.objects.filter(
        campaign__user=user, status='in_flight', queued_at__lt=cutoff,
    )
    if not stuck_qs.exists():
        return 0

    # Materialize once so we can capture (id, attempts) before the bulk
    # UPDATEs flip rows out from under us. Then two bulk UPDATEs (one
    # "failed at max attempts", one "back to queued") replace the prior
    # per-row Python loop with N saves. For users with ~50 stranded rows
    # this drops cleanup from ~11s to <500ms.
    stuck = list(stuck_qs.values('id', 'attempts'))
    abandon_ids = [s['id'] for s in stuck if (s['attempts'] or 0) >= MAX_ATTEMPTS]
    requeue_ids = [s['id'] for s in stuck if (s['attempts'] or 0) < MAX_ATTEMPTS]
    attempts_by_id = {s['id']: s['attempts'] for s in stuck}
    affected_campaign_ids = list(stuck_qs.values_list('campaign_id', flat=True).distinct())

    now = timezone.now()
    abandoned = OutreachAction.objects.filter(id__in=abandon_ids).update(
        status='failed',
        completed_at=now,
        last_error='stale_inflight_max_attempts',
    )
    requeued = OutreachAction.objects.filter(id__in=requeue_ids).update(status='queued')

    # Audit-log the recovery as a single bulk_create — N INSERTs collapsed
    # into one. The events keep the existing actor='server_recovery'
    # contract that downstream tooling + tests check for.
    events = []
    for sid in abandon_ids:
        events.append(OutreachActionEvent(
            action_id=sid, from_status='in_flight', to_status='failed',
            actor='server_recovery', reason='stale_inflight_max_attempts',
            detail='', attempts_after=attempts_by_id.get(sid, 0),
        ))
    for sid in requeue_ids:
        events.append(OutreachActionEvent(
            action_id=sid, from_status='in_flight', to_status='queued',
            actor='server_recovery', reason='stale_inflight_requeued',
            detail='', attempts_after=attempts_by_id.get(sid, 0),
        ))
    if events:
        try:
            OutreachActionEvent.objects.bulk_create(events)
        except Exception as exc:  # event log is observability, never block dispatch
            logger.warning("outreach: bulk event log write failed: %s", exc)

    if requeued or abandoned:
        logger.info(
            "outreach: reclaimed %d stale in_flight actions, abandoned %d at max attempts (user=%s)",
            requeued, abandoned, getattr(user, 'id', '?'),
        )
        # Refresh summary on every campaign that had a touched action.
        affected = OutreachCampaign.objects.filter(id__in=affected_campaign_ids)
        for c in affected:
            refresh_campaign_summary(c)
    return requeued + abandoned


def claim_next_action(user) -> Optional[OutreachAction]:
    """Return the next queued action for `user`, or None if nothing dispatchable.

    Marks the returned action as `in_flight` and bumps `attempts` so a crashed
    extension does not silently re-claim the same action forever.
    Also bails when a running campaign for this user is at its daily cap.

    Performance fix (2026-04-28): the prior implementation always called
    reclaim_stale_inflight() FIRST, which did a Python loop with two saves
    + an event-log INSERT per stale row. With ~50 stranded in_flight rows
    from old test sessions, each poll cost ~11s on cleanup before even
    getting to the actual claim query — and the Chrome extension polls
    this on a tight loop. That hogged Django dev server's thread pool and
    blocked the user's gap-analysis fetch.

    Now: check for running campaigns FIRST. When the user has none, skip
    cleanup entirely (nothing dispatchable, nothing to clean up that
    matters). When campaigns are running, do cleanup as a single bulk
    UPDATE instead of iterating with per-row saves.
    """
    running_campaigns = OutreachCampaign.objects.filter(
        user=user, status='running'
    ).values_list('id', 'daily_invite_cap')

    # Fast path: no campaigns running → nothing to dispatch, nothing
    # urgent to reclaim. Return immediately and let the periodic
    # housekeeping job (or a manual recovery) handle stragglers.
    if not running_campaigns:
        return None

    # Now that we know there's at least one running campaign, recover
    # any stranded in_flight actions before claiming.
    reclaim_stale_inflight(user)

    sent_today = invites_sent_today(user)
    cap = max(cap for _, cap in running_campaigns)
    if sent_today >= cap:
        return None

    action = (
        OutreachAction.objects
        .filter(campaign_id__in=[cid for cid, _ in running_campaigns], status='queued')
        .order_by('queued_at')
        .first()
    )
    if action is None:
        return None

    prev_status = action.status
    action.status = 'in_flight'
    action.attempts = action.attempts + 1
    action.save(update_fields=['status', 'attempts'])
    _log_event(action, from_status=prev_status, to_status='in_flight',
               actor='server_dispatch', reason='claimed')
    refresh_campaign_summary(action.campaign)
    return action


def record_action_result(action: OutreachAction, status: str, error: str = '') -> OutreachAction:
    """Apply an extension-reported outcome to `action` and check campaign completion."""
    valid_statuses = {'sent', 'accepted', 'failed', 'skipped'}
    if status not in valid_statuses:
        raise ValueError(f"unsupported status {status!r}; expected one of {valid_statuses}")

    prev_status = action.status
    action.status = status
    action.last_error = error or ''
    if status in {'sent', 'accepted', 'skipped'}:
        action.completed_at = timezone.now()
    elif status == 'failed' and action.attempts >= MAX_ATTEMPTS:
        action.completed_at = timezone.now()
    action.save(update_fields=['status', 'last_error', 'completed_at'])

    _log_event(action, from_status=prev_status, to_status=status,
               actor='extension', reason=error or 'outcome', detail=error)

    _maybe_finish_campaign(action.campaign)
    return action


def refresh_campaign_summary(campaign: OutreachCampaign) -> dict:
    """Recompute and persist the campaign's per-status action counts.

    Called from every place that transitions an action's state. Cheap
    (one COUNT(*) GROUP BY status query per campaign) and gives the
    status panel an O(1) render instead of re-aggregating on every poll.

    Also bumps last_activity_at — needed for the eventual stale-campaign
    cleanup (any campaign with last_activity_at older than N days and
    no open actions can be auto-completed). Even without that cleanup
    in place, the timestamp is useful for debugging "is this campaign
    actually doing anything?".

    Returns the new stats dict.
    """
    from django.db.models import Count
    rows = (
        campaign.actions
        .values('status')
        .annotate(n=Count('id'))
    )
    by_status = {r['status']: r['n'] for r in rows}
    stats = {
        'queued':    by_status.get('queued', 0),
        'in_flight': by_status.get('in_flight', 0),
        'sent':      by_status.get('sent', 0),
        'accepted':  by_status.get('accepted', 0),
        'failed':    by_status.get('failed', 0),
        'skipped':   by_status.get('skipped', 0),
    }
    stats['total'] = sum(stats.values())
    campaign.summary_stats = stats
    campaign.last_activity_at = timezone.now()
    campaign.save(update_fields=['summary_stats', 'last_activity_at', 'updated_at'])
    return stats


def _maybe_finish_campaign(campaign: OutreachCampaign) -> None:
    """Auto-transition campaign to done/failed when no actions are open.

    Always refresh the summary cache (so the status panel reflects the
    latest counts even when this call is a no-op), then check whether
    the campaign has settled.
    """
    refresh_campaign_summary(campaign)

    open_actions = campaign.actions.filter(status__in=['queued', 'in_flight']).exists()
    if open_actions:
        return
    failed_only = not campaign.actions.filter(status__in=['sent', 'accepted']).exists()
    if failed_only and campaign.actions.exists():
        campaign.status = 'failed'
    else:
        campaign.status = 'done'
    campaign.save(update_fields=['status', 'updated_at'])
