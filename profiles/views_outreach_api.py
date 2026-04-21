"""HTTP endpoints for the outreach automation feature.

Two surfaces live here:
  * Token-authed JSON endpoints called by the Chrome extension
    (`/api/outreach/next`, `/api/outreach/result/<id>/`).
  * Session-authed JSON endpoints called by the SmartCV web UI
    (`/api/outreach/campaigns/`, `/api/outreach/campaigns/<id>/pause/`,
    `/api/outreach/campaigns/<id>/status/`).
"""

import json
import logging
import uuid
from functools import wraps

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from jobs.models import Job
from profiles.models import OutreachAction, OutreachCampaign
from profiles.services.outreach_dispatcher import (
    claim_next_action,
    invites_sent_today,
    record_action_result,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def extension_token_required(view):
    """Auth decorator for extension-facing endpoints.

    Reads `Authorization: Token <uuid>` and resolves to the User whose
    `outreach_token` matches. Returns 401 on missing/invalid tokens. Used
    instead of @login_required because the extension never holds the user's
    Django session cookie — only the opaque, revocable outreach token.
    """
    @wraps(view)
    @csrf_exempt
    def wrapper(request, *args, **kwargs):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.startswith('Token '):
            return JsonResponse({'error': 'missing_token'}, status=401)
        raw = header[len('Token '):].strip()
        try:
            token = uuid.UUID(raw)
        except ValueError:
            return JsonResponse({'error': 'invalid_token'}, status=401)
        try:
            user = User.objects.get(outreach_token=token)
        except User.DoesNotExist:
            return JsonResponse({'error': 'unknown_token'}, status=401)
        request.outreach_user = user
        return view(request, *args, **kwargs)
    return wrapper


def _action_to_payload(action: OutreachAction) -> dict:
    return {
        'id': str(action.id),
        'kind': action.kind,
        'target_handle': action.target_handle,
        'target_name': action.target_name,
        'target_role': action.target_role,
        'payload': action.payload,
        'profile_url': f'https://www.linkedin.com/in/{action.target_handle}/',
    }


# ─── Extension-facing endpoints ────────────────────────────────────────────

@require_http_methods(['GET'])
@extension_token_required
def outreach_next(request):
    """Return the oldest queued action for this user, or 204 if nothing to do."""
    action = claim_next_action(request.outreach_user)
    if action is None:
        return HttpResponse(status=204)
    return JsonResponse(_action_to_payload(action))


@require_http_methods(['POST'])
@extension_token_required
def outreach_result(request, action_id):
    """Extension reports the outcome of a previously claimed action."""
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'bad_json'}, status=400)

    status = body.get('status')
    error = body.get('error', '')
    action = get_object_or_404(
        OutreachAction,
        id=action_id,
        campaign__user=request.outreach_user,
    )
    try:
        record_action_result(action, status, error)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'status': action.status})


# ─── Session-authed (web UI) endpoints ─────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def draft_manual_target(request):
    """Generate a connect-message draft for a single user-supplied target.

    Used by the "Add target manually" path on the campaign builder when the
    server-side discovery (Google + public hiring team) returns nothing —
    the user copies a LinkedIn profile URL out of their own logged-in tab
    and pastes it in. We extract the handle, run the per-target LLM, and
    return the draft + dataclass-shaped target dict that the Alpine UI
    appends to its discovered list.
    """
    from jobs.services.people_finder import _extract_handle, Target
    from profiles.services.outreach_generator import generate_outreach_for_target
    from profiles.models import UserProfile

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'bad_json'}, status=400)

    job_id = body.get('job_id')
    raw = (body.get('handle_or_url') or '').strip()
    name = (body.get('name') or '').strip()[:128]
    role = (body.get('role') or '').strip()[:128]
    if not job_id or not raw:
        return JsonResponse({'error': 'missing_job_or_handle'}, status=400)

    job = get_object_or_404(Job, id=job_id, user=request.user)
    profile = get_object_or_404(UserProfile, user=request.user)

    # Accept either a vanity slug or any /in/<slug>/ URL
    handle = _extract_handle(raw) or raw.lower().strip('/').split('/')[-1]
    if not handle or '/' in handle or len(handle) > 128:
        return JsonResponse({'error': 'unparseable_handle'}, status=400)

    target = Target(handle=handle, name=name or handle, role=role or 'someone at the company', source='manual')
    drafts = generate_outreach_for_target(profile, job, target)

    return JsonResponse({
        'target': target.to_dict(),
        'draft': drafts,
    })


@login_required
@require_http_methods(['POST'])
def create_campaign(request):
    """Create a campaign + queued OutreachAction rows from the web UI."""
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'bad_json'}, status=400)

    job_id = body.get('job_id')
    targets = body.get('targets') or []
    daily_cap = int(body.get('daily_invite_cap', 15))
    if daily_cap < 1 or daily_cap > 25:
        return JsonResponse({'error': 'daily_invite_cap_out_of_range'}, status=400)
    if not job_id or not targets:
        return JsonResponse({'error': 'missing_job_or_targets'}, status=400)

    job = get_object_or_404(Job, id=job_id, user=request.user)
    campaign = OutreachCampaign.objects.create(
        user=request.user,
        job=job,
        status='running',
        daily_invite_cap=daily_cap,
    )

    created = 0
    for target in targets:
        handle = (target.get('handle') or '').strip().lower()
        message = (target.get('message') or '').strip()
        if not handle or not message:
            continue
        OutreachAction.objects.get_or_create(
            campaign=campaign,
            target_handle=handle,
            kind='connect',
            defaults={
                'target_name': target.get('name', '')[:128],
                'target_role': target.get('role', '')[:128],
                'payload': message[:300],  # LinkedIn connect-note limit
            },
        )
        created += 1

    return JsonResponse({
        'campaign_id': str(campaign.id),
        'queued': created,
    })


@login_required
@require_http_methods(['POST'])
def pause_campaign(request, campaign_id):
    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, user=request.user)
    campaign.status = 'paused' if campaign.status == 'running' else 'running'
    campaign.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'status': campaign.status})


@login_required
@require_http_methods(['GET'])
def campaign_status(request, campaign_id):
    """Live status panel data — polled by the campaign UI every ~5s."""
    campaign = get_object_or_404(OutreachCampaign, id=campaign_id, user=request.user)
    actions = list(campaign.actions.values(
        'id', 'target_handle', 'target_name', 'target_role',
        'kind', 'status', 'last_error', 'attempts', 'completed_at',
    ))
    return JsonResponse({
        'campaign': {
            'id': str(campaign.id),
            'status': campaign.status,
            'daily_invite_cap': campaign.daily_invite_cap,
            'sent_today': invites_sent_today(request.user),
        },
        'actions': [
            {**a, 'id': str(a['id']),
             'completed_at': a['completed_at'].isoformat() if a['completed_at'] else None}
            for a in actions
        ],
    })


# ─── Pairing page (issues the extension token) ─────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def pairing_view(request):
    """Show the user their outreach token; POST regenerates it."""
    user = request.user
    rotated = False
    if request.method == 'POST' or user.outreach_token is None:
        user.rotate_outreach_token()
        rotated = request.method == 'POST'
    return render(request, 'profiles/outreach_pair.html', {
        'token': str(user.outreach_token),
        'rotated': rotated,
    })
