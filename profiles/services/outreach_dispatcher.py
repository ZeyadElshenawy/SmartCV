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

from profiles.models import OutreachAction, OutreachCampaign

logger = logging.getLogger(__name__)


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


def claim_next_action(user) -> Optional[OutreachAction]:
    """Return the next queued action for `user`, or None if nothing dispatchable.

    Marks the returned action as `in_flight` and bumps `attempts` so a crashed
    extension does not silently re-claim the same action forever.
    Also bails when a running campaign for this user is at its daily cap.
    """
    running_campaigns = OutreachCampaign.objects.filter(
        user=user, status='running'
    ).values_list('id', 'daily_invite_cap')

    if not running_campaigns:
        return None

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

    action.status = 'in_flight'
    action.attempts = action.attempts + 1
    action.save(update_fields=['status', 'attempts'])
    return action


def record_action_result(action: OutreachAction, status: str, error: str = '') -> OutreachAction:
    """Apply an extension-reported outcome to `action` and check campaign completion."""
    valid_statuses = {'sent', 'accepted', 'failed', 'skipped'}
    if status not in valid_statuses:
        raise ValueError(f"unsupported status {status!r}; expected one of {valid_statuses}")

    action.status = status
    action.last_error = error or ''
    if status in {'sent', 'accepted', 'skipped'}:
        action.completed_at = timezone.now()
    elif status == 'failed' and action.attempts >= 3:
        action.completed_at = timezone.now()
    action.save(update_fields=['status', 'last_error', 'completed_at'])

    _maybe_finish_campaign(action.campaign)
    return action


def _maybe_finish_campaign(campaign: OutreachCampaign) -> None:
    open_actions = campaign.actions.filter(status__in=['queued', 'in_flight']).exists()
    if open_actions:
        return
    failed_only = not campaign.actions.filter(status__in=['sent', 'accepted']).exists()
    if failed_only and campaign.actions.exists():
        campaign.status = 'failed'
    else:
        campaign.status = 'done'
    campaign.save(update_fields=['status', 'updated_at'])
