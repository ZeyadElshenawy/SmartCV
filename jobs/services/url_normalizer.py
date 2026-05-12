"""URL normalization for RecommendedJob dedup.

Strips query params and trailing slashes. Indeed listings keep the `jk=`
query (without it the URL doesn't resolve to the right page) — Indeed
URLs are constructed at scrape time as `/viewjob?jk=...`, so the
normalizer below is a fallback for sources that don't carry that
constraint. We special-case `viewjob?jk=` so dedup still works on Indeed.
"""

from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    parsed = urlparse(url)
    if not parsed.scheme:
        return url

    # Indeed: keep the canonical job-key (jk) but drop everything else.
    if parsed.netloc.endswith("indeed.com") and parsed.path == "/viewjob":
        kept = [(k, v) for (k, v) in parse_qsl(parsed.query) if k == "jk"]
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            "", urlencode(kept), "",
        ))

    # Glassdoor: keep `jl` (job listing id), drop the rest.
    if parsed.netloc.endswith("glassdoor.com") and "/job-listing/" in parsed.path:
        kept = [(k, v) for (k, v) in parse_qsl(parsed.query) if k == "jl"]
        path = parsed.path.rstrip("/")
        return urlunparse((
            parsed.scheme, parsed.netloc, path,
            "", urlencode(kept), "",
        ))

    # Default: drop query + fragment, strip trailing slash from path.
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
