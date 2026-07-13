"""Shared helpers for route modules — template env, redirect, safe conversion."""

import os
import jinja2
from fastapi.responses import RedirectResponse
from urllib.parse import quote_plus
from hermesclaw.scoring import clamp

_DASHBOARD_SCOPE_ALL = "all"

_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "templates")
    ),
    autoescape=True,
    cache_size=0,
)


def _dashboard_redirect(
    msg: str,
    msg_key: str = "optimizer_msg",
    query: str = "",
    selected_scope: str = _DASHBOARD_SCOPE_ALL,
    page: int = 1,
    health_stale_days: int = 14,
    health_confidence_threshold: float = 0.3,
    health_limit: int = 8,
) -> RedirectResponse:
    page = clamp(page, 1, 100000)
    health_stale_days = clamp(health_stale_days, 1, 365)
    health_confidence_threshold = clamp(health_confidence_threshold, 0.0, 1.0)
    health_limit = clamp(health_limit, 1, 100)
    qs = f"?{msg_key}={quote_plus(msg)}&query={quote_plus(query)}&selected_scope={quote_plus(selected_scope)}"
    qs += f"&page={page}&health_stale_days={health_stale_days}&health_confidence_threshold={health_confidence_threshold}&health_limit={health_limit}"
    return RedirectResponse(url=f"/dashboard{qs}", status_code=303)


def safe_int(val, default=0, lo=None, hi=None):
    try:
        v = int(val)
        if lo is not None:
            v = max(v, lo)
        if hi is not None:
            v = min(v, hi)
        return v
    except (TypeError, ValueError):
        return default


def safe_float(val, default=0.0, lo=None, hi=None):
    try:
        v = float(val)
        if lo is not None:
            v = max(v, lo)
        if hi is not None:
            v = min(v, hi)
        return v
    except (TypeError, ValueError):
        return default
