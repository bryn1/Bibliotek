#!/usr/bin/env python3
"""vm106 service entrypoint for the Bibliotek library app (MC 1924.2).

Runs the FastAPI app (src.app:app) with uvicorn, bound to 127.0.0.1:8140
(the manifest port for apps/bibliotek, matching its historical port),
modeled on apps/hotell/server.py's chdir + $STATE_DIRECTORY pattern.

Three things needed fixing to run read-only + behind the /bibliotek/
prefix, none of them an edit to the fork's own route/template files:

1. SQLITE DB: src/config.py already reads DATABASE_URL from the
   environment (a bare os.getenv, not pydantic BaseSettings, but already
   env-overridable exactly like hotell's config.py) -- we set it here to
   $STATE_DIRECTORY/bibliotek.db before `src.app` (and therefore
   src.config/src.database) is ever imported. No fork edit needed.

2. CATALOG DATA (rewired 2026-08-17, MC 1932.2): src/app.py's own
   startup() hook (registered at import time below) now runs the real
   catalog population itself via src/bulk_import.py -- it streams
   data/bulk_books.jsonl.gz (shipped in this repo) into the resolved db
   file with raw stdlib sqlite3, gated on its own version marker file
   next to the db so a restart against an unchanged artifact is a few
   stat() calls, never a re-import. It falls back to the small 3-book
   demo seed only when no bulk artifact is bundled (a bare dev checkout).
   This is unconditional -- it runs the same way under $STATE_DIRECTORY
   or in local dev -- so this file no longer needs a second startup
   handler to seed anything. (Until 2026-08-17 this file registered its
   own second FastAPI startup handler that unconditionally ran
   scripts/seed.py after src.app's own startup() -- that would have
   layered 3 unversioned demo rows on top of every real bulk-imported
   catalog on first boot. scripts/seed.py itself is unchanged and still
   works as a manual local-seeding utility (`python scripts/seed.py`);
   it is simply no longer invoked from here.) The DB file itself is
   never committed to git (created fresh under $STATE_DIRECTORY on first
   boot; the bulk artifact that populates it is committed, deliberately,
   as data/bulk_books.jsonl.gz).

3. /bibliotek/ PREFIX (F-01, MC 1403.1): the URL prefix is CONFIGURABLE via
   BIBLIOTEK_URL_PREFIX -- default EMPTY, i.e. local dev serves at the root
   with no rewriting at all; production sets BIBLIOTEK_URL_PREFIX=/bibliotek.
   In prefix mode nginx's generated location strips the /bibliotek/
   prefix before proxying (proxy_pass ... trailing "/" against a
   location /bibliotek/ {} block is a standard nginx prefix-replace
   rewrite), so the app itself never sees it. Unlike hotell/rocket, this
   fork's templates hardcode ABSOLUTE paths (href="/static/...",
   href="/login", action="/catalog", etc. -- grepped, zero use of
   url_for()), so FastAPI/uvicorn's root_path mechanism (which only
   rewrites url_for()-generated URLs) would not reach them, and neither
   would a bare root_path setting for the 303 redirects login/register/
   return-book issue (Location: /, /login, /loans -- no prefix). A small
   Starlette middleware below rewrites both: HTML response bodies'
   href=/src=/action="/..." attributes, and redirect responses' Location
   headers, to carry the configured prefix. Contained entirely in this
   file -- zero edits to src/ or templates/. The same middleware also
   STRIPS the prefix from incoming request paths before routing, so the
   same paths are reachable both at the root (nginx-strip deployments)
   and under the prefix (local prefix-mode checks, where no nginx runs).

4. FETCH-PREFIX (MC 1095.3, T1r fix): the middleware above only reached
   HTML attributes -- it did NOT reach the loan flow's client-side
   fetch() calls. Both the checkout flow (templates/book_detail.html:
   `fetch('/api/loans/checkout-cookie', ...)`) and the return flow
   (templates/loans.html: `fetch('/api/loans/return-cookie', ...)`), and
   the shared api() helper in static/js/app.js (`fetch(path, ...)` for
   admin/catalog/register), issue fetch() to bare /api/... paths that
   nginx strips the /bibliotek prefix from -- so live, behind the
   prefix, the browser posts to /api/loans/... at the nginx level and
   misses the proxied location (Lån / returnera fail with a non-2xx and
   no prefix reachable route). Fixed here by extending the same
   middleware to ALSO rewrite the single-quoted string-literal argument
   of fetch() calls, and to run over static .js responses too (served as
   application/javascript), not just text/html. Still contained entirely
   in this file -- zero edits to src/ or templates/.

With no $STATE_DIRECTORY (local dev) behavior is unchanged: DATABASE_URL
keeps its in-repo default; catalog population is entirely src.app's own
job now (bulk import if the artifact is present, else the demo seed). With
no BIBLIOTEK_URL_PREFIX (the local-dev default) the prefix middleware is
installed but inert: nothing is stripped from request paths and nothing is
rewritten in responses.
"""
import os
import re
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

PORT = 8140          # MUST match the `port` field in apps.yaml for apps/bibliotek
HOST = "127.0.0.1"   # renderer's hardened unit binds the service to loopback
# F-01 (MC 1403.1): URL prefix, read from the environment. Empty (the
# default) = local dev at the root, no rewriting; production sets
# BIBLIOTEK_URL_PREFIX=/bibliotek. No trailing slash.
PREFIX = os.environ.get("BIBLIOTEK_URL_PREFIX", "").rstrip("/")

STATE_DIR = os.environ.get("STATE_DIRECTORY")

if STATE_DIR:
    db_path = os.path.join(STATE_DIR, "bibliotek.db")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")

# No SECRET_KEY fallback here (F-03): src/config.py demands SECRET_KEY (>=16)
# + JWT_EXPIRE_HOURS at import time and MUST be allowed to fail loudly when
# they are absent — a hardcoded fallback secret public in the repo enables
# forged admin JWTs. Set them in the environment or a .env file.

import uvicorn  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.responses import Response as StarletteResponse  # noqa: E402

from src.app import app  # noqa: E402  (import after DATABASE_URL is set above)
# src.app's own @app.on_event("startup") (registered above, at import time)
# already runs the bulk import / demo-seed fallback -- see docstring point 2.
# No second startup handler needed here.


# Rewrites bare absolute paths in HTML attributes: href="/static/..",
# src="/app.js", action="/catalog". The negative lookahead keeps an
# already-prefixed path from being double-prefixed, so the rewrite is
# idempotent. Built from the configured prefix (empty prefix = inert).
def _attr_re(prefix: str):
    p = re.escape(prefix.lstrip("/"))
    return re.compile(rb'(href|src|action)="(/(?!%s(?:/|"))[^"]*)"' % p.encode())


# Rewrites the string-literal path argument of client-side fetch() calls --
# the piece _attr_re cannot see, because they live inside <script> bodies
# and .js files, not in HTML attributes. Both the checkout flow
# (book_detail.html) and the return flow (loans.html) drive the loan
# lifecycle with fetch('/api/loans/...'), and the shared api() helper in
# static/js/app.js issues fetch(path,...) for admin/catalog/register too.
# Same idempotent negative lookahead. Single-quoted only: that is the
# form every fetch() call in this app uses (grepped).
# Group 1 = `fetch('` literal, group 2 = the bare path, group 3 = `'`.
def _fetch_re(prefix: str):
    p = re.escape(prefix.lstrip("/"))
    return re.compile(rb"(\bfetch\s*\(\s*')(/(?!%s(?:/|\"))[^']*)(')" % p.encode())


class PathPrefixMiddleware(BaseHTTPMiddleware):
    """When a prefix is configured: strip it from incoming request paths
    before routing (so the same paths are reachable under the prefix, e.g.
    in local prefix-mode checks where no nginx strips anything), and
    rewrite hardcoded absolute href=/src=/action="/..." attributes in
    HTML responses and fetch('/...') literals in HTML <script> bodies and
    static .js files, plus redirect Location headers, to carry the
    prefix -- see module docstring points 3 and 4. With an empty prefix
    (local dev default) the middleware is inert."""

    def __init__(self, app, prefix: str):
        # MC#2317 pin refresh (fastapi 0.115.0) pulls in a newer Starlette whose
        # build_middleware_stack() calls `cls(app=app, *args, **kwargs)` --
        # i.e. it binds by the KEYWORD "app", not positionally. The old pins
        # (starlette==1.6.0, called middleware positionally) tolerated this
        # constructor's original param name "asgi_app"; the newer resolved
        # Starlette does not (TypeError: unexpected keyword argument 'app'),
        # so the param is renamed to match Starlette's real contract instead
        # of re-pinning starlette to dodge it.
        super().__init__(app)
        self.prefix = prefix  # e.g. "/bibliotek", no trailing slash
        self._attr_re = _attr_re(prefix)
        self._fetch_re = _fetch_re(prefix)

    async def dispatch(self, request, call_next):
        if self.prefix and request.scope.get("path", "").startswith(self.prefix):
            # Strip the configured prefix so the app's root-level routes
            # handle the request (mirrors what nginx does in production).
            stripped = request.scope["path"][len(self.prefix):] or "/"
            request.scope["path"] = stripped

        response = await call_next(request)

        loc = response.headers.get("location")
        if loc and loc.startswith("/") and not loc.startswith(self.prefix + "/") \
                and loc != self.prefix:
            response.headers["location"] = self.prefix + loc

        content_type = response.headers.get("content-type", "")
        # text/html = a rendered page whose <script> bodies carry fetch();
        # text/javascript / application/javascript = the static .js files
        # (app.js etc.) served as-is, which the previous middleware never
        # touched. The "<javascript>" matches both spellings browsers send.
        if self.prefix and ("text/html" in content_type or "javascript" in content_type):
            body = b"".join([chunk async for chunk in response.body_iterator])
            new_body = self._attr_re.sub(
                lambda m: m.group(1) + b'="' + self.prefix.encode() + m.group(2) + b'"',
                body,
            )
            new_body = self._fetch_re.sub(
                lambda m: m.group(1) + self.prefix.encode() + m.group(2) + m.group(3),
                new_body,
            )
            headers = dict(response.headers)
            headers["content-length"] = str(len(new_body))
            response = StarletteResponse(
                content=new_body, status_code=response.status_code,
                headers=headers, media_type=response.media_type,
            )
        return response


app.add_middleware(PathPrefixMiddleware, prefix=PREFIX)

if __name__ == "__main__":
    # reload=False: the service unit owns lifecycle; no file-watch overload.
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
