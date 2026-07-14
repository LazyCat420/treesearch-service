"""Canonical image-URL normalization for scraped forum posts.

XenForo boards (Rollitup, THCFarmer, ICMag) serve remote images through a
`proxy.php?image=<encoded-url>` wrapper. Storing the wrapper is useless — it is
hotlink-gated and expires — so we unwrap it to the direct image URL.
"""

import re
import urllib.parse


def clean_forum_image_url(url: str) -> str:
    """Return the direct image URL, unwrapping a XenForo proxy.php link if present."""
    if not url:
        return ""

    if "proxy.php?image=" not in url:
        return url

    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        image_param = query.get("image")
        if image_param:
            return image_param[0]
    except Exception:
        # Fall back to a regex if the URL is malformed enough to break the parser.
        match = re.search(r"[?&]image=([^&]+)", url)
        if match:
            return urllib.parse.unquote(match.group(1))

    return url
