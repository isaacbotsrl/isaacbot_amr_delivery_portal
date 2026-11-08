"""A tiny JSON-over-HTTP helper on urllib (no third-party client needed)."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Dict, Optional

USER_AGENT = 'isaacbot-delivery-portal/0.1'


class HttpError(RuntimeError):
    pass


def request_json(url: str, method: str = 'GET', body: Optional[Dict] = None,
                 headers: Optional[Dict[str, str]] = None, timeout: float = 10.0,
                 basic_password: str = '') -> Dict:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {'User-Agent': USER_AGENT, 'Accept': 'application/json', **(headers or {})}
    if data is not None:
        hdrs.setdefault('Content-Type', 'application/json')
    if basic_password:
        hdrs['Authorization'] = 'Basic ' + base64.b64encode(
            f'portal:{basic_password}'.encode()).decode()
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 configured URLs
            return json.loads(resp.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors='replace')[:300]
        raise HttpError(f'{method} {url.split("?")[0]}: HTTP {e.code} {detail}') from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(f'{method} {url.split("?")[0]}: {e}') from e
    except json.JSONDecodeError as e:
        raise HttpError(f'{method} {url.split("?")[0]}: not JSON ({e})') from e
