"""Context processors that run for every rendered template.

Keep these lean — they run on every request. Prefer session lookups over
DB queries here.
"""


def onboarding(request):
    """Expose `in_onboarding` to every template.

    Set to True by welcome_view when a fresh signup lands on /welcome/ and
    sees the three-way chooser. Cleared by skip_onboarding_view or when the
    user naturally reaches the dashboard. Existing users who log in without
    going through /welcome/ never have this flag set, so their pages don't
    show the skip button.
    """
    return {'in_onboarding': bool(request.session.get('in_onboarding'))}
