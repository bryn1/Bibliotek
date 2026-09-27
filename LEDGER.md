# Bibliotek Build LEDGER

**Goal:** Bibliotekssystem med utlåning/inlämning (28 dagar), ISBN-streckkod via mobilkamera, HCF-integration för svensk bokidentifiering, roller (användare, bibliotekarie, admin).

**Acceptance:** Webapp kör på localhost:8140. Kameran skannar streckkod → söker bok → bibliotekarie kan låna ut → inlämning räknar tillbaka 28 dagar.

## Modules

|| Module | Concern | Status |
||---|---|---|
|| PLAN.md | Modular plan from Planner | ✅ done (job 15) |
|| DESIGN.md | Data model, interface contracts | ✅ done |
|| src/database.py | SQLAlchemy models + session | ✅ done + verified (job 17) |
|| src/models.py | Pydantic schemas (requests/responses) | ✅ done + verified |
|| src/auth.py | JWT auth, password hashing, role middleware | ✅ done + verified (job 18) |
|| src/books.py | Book CRUD + ISBN/EAN lookup + HCF API | ✅ done + verified (job 19) |
|| src/circulation.py | Checkout/return, 28-day due date, overdue | ✅ done + verified (job 20) |
|| src/users.py | User management (librarian CRUD) | ✅ done + verified (job 21) |
|| src/app.py | FastAPI app, routers, static files | ✅ done + verified (job 21) |
|| templates/ | HTML templates (Jinja2) | ✅ done + verified (job 22) |
|| static/ | CSS/JS (camera scanner) | ✅ done + verified (job 22) |
|| tests/ | Unit + E2E tests | ✅ done + verified (job 23) |
| **Server** | **Live on port 8140** | **✅ deployed (job 24)** |

## Audit 2026-09-26 fix round (MC 1403.1)

- Full audit (browser click-through + security/correctness children): verdict FIX; report in .tmp/audit-20260926/report.md
- Owner ruling: open admin login is ratified PoC design (F-02 not a bug)
- Fixed: F-01 configurable BIBLIOTEK_URL_PREFIX (59a7957), F-03 no JWT secret fallback (59a7957), F-04 CSRF fields on staff forms (ba4436e), F-11 pagination guards (ba4436e), F-05 dead admin.js fetch layer removed (80c3a4a)
- Verified: 163/163 pytest PASS + live contract checks (prefix both modes, CSRF 403/303, zero admin.js console errors) — TEST verdict PASS
- Known open: F-06 multi-ISBN barcode lookup, F-07 librarian→admin password reset, F-08 GET return mutation, F-09 books.html XSS, F-13 .audits/ root-locked
