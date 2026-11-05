"""Server-side proxy for the model service behind 模型服务接入.

The page is served from a loopback origin, so a browser request straight to
the upstream would die on CORS; fetching here also keeps the key off the page.
"""

from __future__ import annotations

import json
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MODELS = 500


def models_url(base_url) -> str:
    """Normalise a chat-completions base URL into its /models endpoint."""
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        raise ValueError("还没有填写服务地址。")
    if not text.lower().startswith(("http://", "https://")):
        raise ValueError("服务地址要以 http:// 或 https:// 开头。")
    if text.endswith("/models"):
        return text
    return "{}/models".format(text)


def fetch_models(base_url, api_key, timeout=8.0) -> List[Dict[str, str]]:
    url = models_url(base_url)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer {}".format(str(api_key or "")),
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise ValueError("上游返回 HTTP {}，模型列表没取到。".format(status))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise ValueError("上游返回 HTTP {}，模型列表没取到。".format(error.code)) from error
    except URLError as error:
        raise ValueError("连不上上游：{}".format(getattr(error, "reason", error))) from error
    except OSError as error:
        raise ValueError("连不上上游：{}".format(error)) from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("上游返回的内容过大，已丢弃。")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("上游返回的不是 JSON，模型列表没取到。") from error
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("上游没有给出模型列表。")
    models = []
    seen = set()
    for item in items:
        name = item.get("id") if isinstance(item, dict) else None
        name = str(name).strip() if name else ""
        if not name or name in seen:
            continue
        seen.add(name)
        models.append({"id": name})
        if len(models) >= MAX_MODELS:
            break
    return models
