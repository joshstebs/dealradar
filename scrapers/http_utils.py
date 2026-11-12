import asyncio
from urllib.parse import urljoin, urlsplit

import httpx


def _safe_redirect(current: str, location: str | None) -> str | None:
    """Only same-host HTTPS redirects are followed.

    Sources move between http and https or add a trailing slash; blindly
    rejecting every redirect made a working feed look like a schema change.
    Cross-host or downgrade redirects stay refused.
    """
    if not location:
        return None
    target = urljoin(current, location.strip())
    try:
        before, after = urlsplit(current), urlsplit(target)
    except ValueError:
        return None
    if after.scheme != "https" or after.hostname != before.hostname:
        return None
    if after.port not in (None, 443) or after.username or after.password:
        return None
    return target


async def fetch(http, url, params=None, max_bytes=2_000_000, max_redirects=2):
    """Bounded body, same-host redirects, retries for 429/5xx only.

    The returned response is rebuilt from raw, already-decoded bytes. Its
    original Content-Encoding/Content-Length headers must be dropped or httpx
    tries to decompress the body a second time and raises DecodingError.
    """
    current = url
    redirects = 0
    for attempt in range(3):
        async with http.stream("GET", current, params=params, timeout=15,
                               follow_redirects=False) as response:
            if response.is_redirect:
                target = _safe_redirect(str(response.url), response.headers.get("location"))
                if target is None or redirects >= max_redirects:
                    raise ValueError("Source redirected; endpoint must be revalidated")
                current, params = target, None
                redirects += 1
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                # Long Retry-After should fail, never retry earlier than requested.
                if retry_after:
                    if not retry_after.isdigit() or int(retry_after) > 2:
                        response.raise_for_status()
                    delay = max(float(retry_after), 0.25)
                else:
                    delay = 0.25 * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Source payload exceeds size limit")
                chunks.append(chunk)
            headers = [(key, value) for key, value in response.headers.items()
                       if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
            return httpx.Response(response.status_code, headers=headers,
                                  content=b"".join(chunks), request=response.request)
