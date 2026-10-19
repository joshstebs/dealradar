import asyncio
import httpx


async def fetch(http, url, params=None, max_bytes=2_000_000):
    """Bounded body, no redirects, max two retries for 429/5xx only."""
    for attempt in range(3):
        async with http.stream("GET", url, params=params, timeout=15, follow_redirects=False) as response:
            if response.is_redirect:
                raise ValueError("Source redirected; endpoint must be revalidated")
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
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=b"".join(chunks), request=response.request)
