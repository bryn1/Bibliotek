/* Admin shell helper (MC 743.7, F-05).
 *
 * The /admin pages (dashboard, lantagare, personal) are SERVER-RENDERED
 * (templates/admin_dashboard.html: "NO browser fetch, NO JS required") and
 * their tables/forms work with plain HTML POSTs plus the inline progressive
 * enhancement each template carries. This file therefore contains NO data
 * fetching: the earlier stats/user-table fetch layer was written against an
 * imagined API contract (cookie-only calls to Bearer-gated endpoints, a bare
 * array parse of {"users": [...]}, and an "active" toggle for a field the
 * User model does not have) and only produced console 401/400 noise beside
 * the working server-rendered mechanism. It was removed, not re-wired.
 *
 * The real JSON admin surface, for anyone who needs it (src/admin_api.py,
 * src/users.py — both Bearer-JWT gated via src.auth.require_role):
 *   GET    /api/admin/users            -> {"users": [ ... ]}
 *   PUT    /api/admin/users/{id}       -> JSON body {username|role|email|password}
 *   DELETE /api/admin/users/{id}       -> CSRF header required
 *   GET    /api/users                  -> {"users": [ ... ], "total": n}
 *   PUT    /api/users/{id}             -> query params username|password|role|email
 *
 * CSRF: this file performs no mutating calls itself; server-rendered forms
 * carry a csrf_token hidden field (src.csrf.verify_csrf double-submits it
 * against the "csrf" cookie). Any future fetch-based mutation must send the
 * page's <meta name="csrf-token"> value in the X-CSRF-Token header.
 */

(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : (window.CSRF_TOKEN || "");
  }

  function logout() {
    clearAuth();
    window.location.href = "/";
  }

  /* Expose for the small inline page scripts (admin.html keeps toggleMenu
   * and the admin guard inline so they run before this file). */
  window.AdminDashboard = {
    logout: logout,
    csrfToken: csrfToken,
  };
})();
