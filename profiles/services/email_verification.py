"""Read the LinkedIn email-verification code straight from the inbox via IMAP.

When LinkedIn issues a "let's do a quick verification — enter the code we
sent to your email" challenge mid-login, we can fetch the code
programmatically from the same mailbox and type it in, keeping the
scraper silent. For Gmail you need an **App Password** (Google Account →
Security → 2-Step Verification → App passwords); regular Gmail passwords
don't work for IMAP after 2-Step is on.

Stdlib-only — no third-party dependency.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from email.message import Message

logger = logging.getLogger(__name__)


# Provider → default IMAP host:port. Saves the user from setting
# ``LINKEDIN_IMAP_HOST`` manually in the common case.
_DEFAULT_HOSTS: dict[str, tuple[str, int]] = {
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
}

_LINKEDIN_FROM_FRAGMENTS = ("linkedin.com",)
_CODE_PATTERNS = (
    re.compile(r"verification\s+code[^0-9]{0,40}(\d{4,8})", re.IGNORECASE),
    re.compile(r"\b(\d{6})\b\s+is\s+your\s+(?:verification|sign[- ]in)", re.IGNORECASE),
    re.compile(r">\s*(\d{6})\s*<"),  # commonly wrapped in a styled span
    re.compile(r"\bcode[^0-9]{0,30}(\d{4,8})\b", re.IGNORECASE),
)
_FALLBACK_CODE_RE = re.compile(r"\b(\d{6})\b")


def default_host_for(email_address: str) -> tuple[str, int] | None:
    """Resolve a default IMAP host based on the email's domain."""
    if "@" not in email_address:
        return None
    domain = email_address.rsplit("@", 1)[1].lower().strip()
    return _DEFAULT_HOSTS.get(domain)


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, AttributeError):
        return payload.decode("utf-8", errors="replace")


def _email_body(msg: Message) -> str:
    """Extract the human-readable body from a possibly-multipart email,
    preferring plain text but falling back to HTML."""
    if not msg.is_multipart():
        return _decode_payload(msg)

    plain = ""
    html = ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get("Content-Disposition", "").lower().startswith("attachment"):
            continue
        if ctype == "text/plain" and not plain:
            plain = _decode_payload(part)
        elif ctype == "text/html" and not html:
            html = _decode_payload(part)
    return plain or html


def _extract_code(body: str) -> str:
    """Pull a 4–8 digit code out of the email body. LinkedIn currently
    uses 6 digits but we accept a small range to survive minor changes."""
    if not body:
        return ""
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    for pat in _CODE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    m = _FALLBACK_CODE_RE.search(text)
    return m.group(1) if m else ""


def _from_address_matches_linkedin(msg: Message) -> bool:
    sender = (msg.get("From") or "").lower()
    return any(frag in sender for frag in _LINKEDIN_FROM_FRAGMENTS)


def _scan_inbox_once(M: imaplib.IMAP4_SSL, mark_seen: bool) -> str:
    """One pass over the inbox looking for a recent LinkedIn security
    email. Returns the code, or '' if none found this round."""
    M.select("INBOX")
    status, ids = M.search(None, '(FROM "linkedin.com" UNSEEN)')
    if status != "OK" or not ids or not ids[0]:
        # Some providers auto-mark security mail read — fall back to all.
        status, ids = M.search(None, '(FROM "linkedin.com")')
        if status != "OK" or not ids or not ids[0]:
            return ""
    message_ids = ids[0].split()
    # Newest first — LinkedIn sends multiple emails over time.
    for mid in reversed(message_ids[-5:]):
        status, msg_data = M.fetch(mid, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        if not _from_address_matches_linkedin(msg):
            continue
        code = _extract_code(_email_body(msg))
        if code:
            if mark_seen:
                try:
                    M.store(mid, "+FLAGS", r"(\Seen)")
                except imaplib.IMAP4.error:
                    pass
            return code
    return ""


def fetch_linkedin_verification_code(
    user: str,
    password: str,
    *,
    host: str | None = None,
    port: int = 993,
    timeout: float = 120.0,
    poll_interval: float = 4.0,
    mark_seen: bool = True,
) -> str:
    """Poll the IMAP mailbox until LinkedIn's verification email arrives,
    then return the extracted code. Returns '' on timeout.

    ``host`` defaults to the well-known IMAP host for ``user``'s domain
    (Gmail, Outlook, Yahoo, iCloud), or can be overridden explicitly.
    """
    if not host:
        default = default_host_for(user)
        if default is None:
            raise ValueError(
                "Couldn't infer IMAP host from the email domain. Set "
                "LINKEDIN_IMAP_HOST in your env."
            )
        host, port = default

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with imaplib.IMAP4_SSL(host, port) as M:
                M.login(user, password)
                code = _scan_inbox_once(M, mark_seen=mark_seen)
                try:
                    M.logout()
                except imaplib.IMAP4.error:
                    pass
                if code:
                    return code
        except (imaplib.IMAP4.error, OSError) as exc:
            last_error = exc
            logger.warning("IMAP fetch failed (will retry): %s", exc)
        time.sleep(poll_interval)

    if last_error is not None:
        logger.error("IMAP autofetch giving up after errors: %s", last_error)
    return ""


def imap_credentials_present(host: str, user: str, password: str) -> bool:
    """True iff we have enough to attempt an IMAP login. Host can be empty
    when the user's domain has a known default."""
    if not user or not password:
        return False
    if host:
        return True
    return default_host_for(user) is not None
