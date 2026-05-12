from ._login_base import InteractiveLoginCommand


class Command(InteractiveLoginCommand):
    help = (
        "Open a visible browser, let the user log in to LinkedIn, "
        "then save the session for the scraper to reuse."
    )
    source = "linkedin"
    login_url = "https://www.linkedin.com/login"
    # While the URL still contains 'login' or 'checkpoint', login isn't done.
    # LinkedIn redirects to /feed once the session is established.
    success_url_substring = "login"
    # Belt-and-suspenders: also break out of the wait loop if we can detect
    # the global nav, which only renders for authenticated users.
    post_login_check = "[data-test-global-nav], header.global-nav"
