from ._login_base import InteractiveLoginCommand


class Command(InteractiveLoginCommand):
    help = (
        "Open a visible browser, let the user log in to Glassdoor, "
        "then save the session for the scraper to reuse."
    )
    source = "glassdoor"
    login_url = "https://www.glassdoor.com/profile/login_input.htm"
    success_url_substring = "login"   # any URL still containing 'login' = not done
