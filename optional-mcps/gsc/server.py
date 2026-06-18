#!/usr/bin/env python3
"""First-party Google Search Console MCP server (read-only).

Why first-party and not a catalog/third-party GSC MCP: the BigLobster Google
identity was banned once for bot activity and only reinstated on appeal, so we
deliberately keep *all* code that touches that identity in-repo and auditable —
no external server, no OAuth-as-the-human-account. This server authenticates as
a dedicated **service account** with **read-only** scope
(``webmasters.readonly``) and only ever calls the sanctioned Search Analytics
API, which is metered and does not route through Google's anti-abuse systems.

Zero new dependencies: ``mcp`` ships via the ``[all]`` extra, and
``PyJWT[crypto]`` + ``requests`` are core deps. Service-account auth is the
standard JWT-bearer flow (sign assertion → exchange for an access token →
call the REST API), done by hand to avoid pulling in google-api-python-client.

Credential delivery (see docker/cont-init.d/03-biglobster-config + config.yaml):
the service-account JSON is provided as an env var, base64-encoded
(``GSC_SERVICE_ACCOUNT_B64``, preferred — survives .env round-tripping without
quoting/newline hazards) or raw (``GSC_SERVICE_ACCOUNT_JSON``).
"""
import base64
import binascii
import json
import os
import time
import urllib.parse

import jwt
import requests

# Read-only scope — all the SEO/GEO agent needs and the smallest grant that
# works. The service account is added to the GSC property as a *Restricted*
# (read) user, so even a wider scope here would not grant write access.
_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GSC_BASE = "https://searchconsole.googleapis.com/webmasters/v3"

# Process-lifetime access-token cache. Tokens are valid ~1h; we refresh a
# minute early. The server is a long-lived stdio subprocess, so caching here
# avoids a token exchange on every tool call.
_token_cache = {"access_token": None, "exp": 0}


def _load_service_account() -> dict:
    """Parse the service-account JSON from the environment.

    Prefers the base64 form; falls back to raw JSON. Raises a clear,
    secret-free error so a misconfiguration is obvious in the agent's view.
    """
    b64 = os.environ.get("GSC_SERVICE_ACCOUNT_B64")
    raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON")
    if b64:
        try:
            raw = base64.b64decode(b64).decode("utf-8")
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(
                "GSC_SERVICE_ACCOUNT_B64 is not valid base64 — re-generate with "
                "`base64 -w0 key.json` (no line wrapping)."
            ) from exc
    if not raw:
        raise RuntimeError(
            "No service-account credential found. Set GSC_SERVICE_ACCOUNT_B64 "
            "(preferred) or GSC_SERVICE_ACCOUNT_JSON in the environment."
        )
    try:
        sa = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Service-account credential is not valid JSON: {exc}") from exc
    for field in ("client_email", "private_key"):
        if not sa.get(field):
            raise RuntimeError(f"Service-account JSON is missing required field '{field}'.")
    return sa


def _get_access_token() -> str:
    now = int(time.time())
    if _token_cache["access_token"] and _token_cache["exp"] - 60 > now:
        return _token_cache["access_token"]

    sa = _load_service_account()
    token_uri = sa.get("token_uri", _DEFAULT_TOKEN_URI)
    claims = {
        "iss": sa["client_email"],
        "scope": _SCOPE,
        "aud": token_uri,
        "iat": now,
        "exp": now + 3600,
    }
    headers = {}
    if sa.get("private_key_id"):
        headers["kid"] = sa["private_key_id"]
    assertion = jwt.encode(claims, sa["private_key"], algorithm="RS256", headers=headers)

    resp = requests.post(
        token_uri,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed ({resp.status_code}). Check the service account "
            f"key is valid and the system clock is correct. Body: {resp.text[:300]}"
        )
    tok = resp.json()
    _token_cache["access_token"] = tok["access_token"]
    _token_cache["exp"] = now + int(tok.get("expires_in", 3600))
    return _token_cache["access_token"]


def _request(method: str, path: str, json_body: dict | None = None) -> dict:
    token = _get_access_token()
    resp = requests.request(
        method,
        f"{_GSC_BASE}{path}",
        json=json_body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if resp.status_code == 403:
        raise RuntimeError(
            "403 from GSC. The service account almost certainly lacks access to "
            "this property — add its client_email as a user in Search Console "
            "(Settings → Users and permissions). Body: " + resp.text[:300]
        )
    if resp.status_code != 200:
        raise RuntimeError(f"GSC API error ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - surfaced at launch, not import-time in prod
    raise SystemExit(
        "The 'mcp' package is required. Install with: pip install 'hermes-agent[mcp]'"
    ) from exc

mcp = FastMCP("gsc")


@mcp.tool()
def gsc_list_sites() -> dict:
    """List the Search Console properties the service account can access.

    Use this to verify the credential and property grant are wired correctly
    before relying on the analytics tool.
    """
    return _request("GET", "/sites")


@mcp.tool()
def gsc_search_analytics(
    site_url: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
    row_limit: int = 1000,
    search_type: str = "web",
) -> dict:
    """Query Search Console Search Analytics (clicks, impressions, CTR, position).

    Args:
        site_url: Property URL exactly as registered in GSC, e.g.
            "https://biglobster.top/" or a domain property "sc-domain:biglobster.top".
        start_date: Inclusive start date, "YYYY-MM-DD". GSC retains ~16 months;
            the SEO agent uses at most 90 days.
        end_date: Inclusive end date, "YYYY-MM-DD".
        dimensions: Any of "query", "page", "country", "device", "date",
            "searchAppearance". Defaults to ["query"].
        row_limit: Max rows (1–25000). Defaults to 1000.
        search_type: "web" (default), "image", "video", "news", or "discover".

    Returns the raw GSC response, including the "rows" array.
    """
    if dimensions is None:
        dimensions = ["query"]
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "type": search_type,
    }
    encoded_site = urllib.parse.quote(site_url, safe="")
    return _request("POST", f"/sites/{encoded_site}/searchAnalytics/query", body)


if __name__ == "__main__":
    mcp.run()
