"""URL redaction primitives (SEC-015).

Lives in its own module -- rather than in :mod:`vtg.remote` where the rest of the
remote-source machinery is -- for one reason: the CLI's top-level
``except Exception`` sink must redact the message it surfaces, and that sink must
never be the place where a heavyweight import first happens. ``vtg.remote`` pulls
in ``http.client``/``ssl``/``email`` (~12ms) that a purely local command never
needs, and importing it *while already handling a crash* could mask the original
failure. This module imports nothing beyond ``re`` and ``urllib.parse``, so the
crash path stays import-free and every local command stops paying for the remote
stack. ``vtg.remote`` re-exports both functions, so ``remote.redact_url`` and
``remote.redact_message_urls`` keep resolving for existing callers and tests.
"""

from __future__ import annotations

import re
import urllib.parse


def redact_url(url: str) -> str:
    """Strip userinfo and the entire query/fragment, keeping scheme/host/path.

    The single redaction rule applied to any source URL echoed anywhere. A
    signed-URL token (in the query) and any embedded ``user:pass@`` credentials
    never survive.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme:
        return url
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    port_str = f":{port}" if port else ""
    return f"{parts.scheme.lower()}://{host}{port_str}{parts.path}"


# Any http(s) URL embedded in an arbitrary message string. Used to scrub a URL
# that might otherwise ride along in a free-form error message (e.g. the CLI's
# INTERNAL_ERROR sink), applying the SEC-015 redaction rule as defense in depth.
_URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>]+")


def redact_message_urls(text: str) -> str:
    """Redact every http(s) URL substring in a free-form message (SEC-015).

    A defensive companion to :func:`redact_url` for messages that are not a bare
    URL: each embedded URL is reduced to scheme/host/path so a signed token or
    embedded credential can never leak through a generic error string.
    """
    return _URL_IN_TEXT.sub(lambda m: redact_url(m.group(0)), text)
