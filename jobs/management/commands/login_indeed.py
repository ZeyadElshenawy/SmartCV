from ._login_base import InteractiveLoginCommand


class Command(InteractiveLoginCommand):
    help = (
        "Open a visible browser, let the user log in to Indeed (magic link / "
        "Google SSO / etc), then save the session for the scraper to reuse."
    )
    source = "indeed"
    login_url = "https://secure.indeed.com/auth"
    success_url_substring = "secure.indeed.com"
    # While we're on secure.indeed.com/* the login isn't done. Login completes
    # when Indeed redirects back to www.indeed.com / myjobs.indeed.com.
