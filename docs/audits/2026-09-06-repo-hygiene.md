# Repository audit and safe hygiene — 2026-09-06

## Scope, evidence, and limits

Starting production/main: `e705c5cb2a9b76d397fbd9f9de49dd0429c534b4`.
Inventory, cross-file reference review, dependency checks, and the complete
non-browser Python baseline finished before edits. No production database,
Google workbook, Render configuration, deployment, or operational record was used.
The existing `desktop-pre-sync-2026-09-03` stash and unrelated untracked
`app/static/images/neoscorpion/spear_intelligent_operations_dashboard.png` were preserved.

The inventory traversed all 862 tracked files: Python AST/imports/routes/query
patterns, 146 Jinja templates, JavaScript/Apps Script, both stylesheets, static
asset hashes and references, requirements, manifests, generators, configuration,
scripts, and documentation. An isolated SQLite-configured factory registered
334 Flask URL rules. Manual review concentrated on security boundaries, live
polling, schema ownership, dynamic asset consumers, and proposed deletions.
This is a repository audit, not a formal proof that every route is secure or
every production query has been profiled.

Local evidence (ignored, not shipped):

- `instance/repo-audit-2026-09-06/inventory-before.json`: complete static asset
  path/size/SHA-256/literal-reference graph, import candidates, routes, metrics.
- `instance/repo-audit-2026-09-06/code-inventory-before.json`: CSS statistics,
  repeated blocks, unresolved class candidates, route/query-loop candidates.
- `instance/repo-audit-2026-09-06/baseline.xml` and `final.xml`: full Python
  results, including individual failures/subfailures.

All 278 tracked static files were hashed and referenced; 39 exact-hash groups
were found, primarily canonical derivative contracts rather than removable
duplicates. Literal-reference absence was only a candidate signal. Dynamic consumers were
traced through `app/__init__.py` PWA specifications, `access_control.py` install
rows, `shell_metadata.py` node identities, Jinja `picture/srcset`, CSS URLs,
scripts, manifests, generators, and historical tests/docs before deletion.

## Implemented findings

| Classification / severity | Evidence at starting commit | Change and verification |
| --- | --- | --- |
| SECURITY HARDENING / Medium | `app/static/js/live_updates.js:852`, `bindPeopleEntryEnhancements`: database unit-path segments were interpolated into `group.innerHTML`. Unit names originate in `neostaffing.py:validated_unit_update_values`; creation/update require `ORG_CHART_EDIT_STRUCTURE_PERMISSION` in `neostaffing/routes.py:1885`. | Build summaries with `textContent`. Null-prototype hierarchy maps also allow literal `__proto__`/`constructor` names without inherited-property collisions. Tests exercise real JS initialization, malicious labels, and selection of the unchanged underlying option ID. |
| SECURITY HARDENING / Medium | `app/templates/neostaffing/people.html:13`: bulk pasted cells were interpolated into `out.innerHTML`. | Build table cells with `textContent`; preserve row numbering, seven-field validation, invalid styling, disabled state, and existing action text. Tests run the actual inline script with HTML-shaped pasted cells. |
| SAFE DELETE / Low | `app/auth/routes.py:user_has_gateway_access` and `app/services/google_motherbrain_live_missions.py:TAIL_STATUS_NORMAL` imports had no AST loads, external consumers, patch targets, template references, or export contract. | Remove only these two imports. No access policy, mission logic, or module initialization removed. |
| CSS CLEANUP / Low | `.login-stage`, `.login-identity`, `.login-panel` and its six descendants existed only in `base.css`; no tracked template/JS uses or constructs these classes. Current `login_hub.html` uses `.portal-login-page`, `.motherbrain-core`, `.command-login-form`. | Remove nine orphan rule blocks and two obsolete selector entries in otherwise preserved grouped rules. 1,357 LF-normalized bytes / 68 lines removed. No active rule values/order changed. |
| SAFE ARCHIVE / Low | Three `neobutton1*` files had no live references; only `_medium` occurred in a negative CSS assertion in `test_local_launch_navigation.py:227`. PWA/icon generators use other explicit naming contracts; no dynamic button-image constructor exists. | Copy/hash-check/archive then remove tracked originals. Three files / 1,260,550 bytes; archive details below. |
| KEEP / Low | `neosubzero_departure_deice.RAMP_ORDER` appeared unused within that module but is imported by `neosubzero/routes.py:25`. | Preserve this compatibility re-export. A trial removal was caught at test collection and reverted before validation; no final change to this file. This is why import scans alone are not deletion proof. |

The two HTML sinks are confirmed HTML-injection defects, **not confirmed
critical/high script-execution exploits**: structure editing is privileged,
the bulk path is paste-driven, and production CSP uses nonced scripts without
`unsafe-inline`. Text rendering removes the unsafe interpretation regardless
of CSP. No auth, permissions, workflow, or visible design was changed.

## Local asset archive

Exact local root:
`Z:\DevProj\NeoApps\_local_archive\unused_assets\2026-09-06\`.
The paths below are preserved underneath that root. Each destination was
checked before copying; SHA-256 equality was verified before removing the
original. `_local_archive/` is ignored and must not be committed.

| Original relative path | Bytes | SHA-256 of original and archived copy |
| --- | ---: | --- |
| `app/static/images/neobutton1.png` | 998,702 | `5c195c2ba6e7278d57f46b0cbd560dd4ac88a8e3cd90f416f52e5595e79e30a2` |
| `app/static/images/neobutton1_medium.png` | 214,570 | `54816cf847122e410cd8da8a579e88909a3192a4e516079cba42c22903875928` |
| `app/static/images/neobutton1_small.png` | 47,278 | `ba7b81b02726705fc54185d0a6d924dd372c58d5cf61fd646037e3c10feb2f46` |

Archive bytes: **1,260,550**. Restoring an individual file is a normal copy
back to its original path; the originals also remain in Git history.
No approved current assets, node icons, masters, derivatives, or fonts removed.

## CSS audit and next decomposition strategy

Counts use UTF-8 with CRLF normalized to LF. Rule/selector counts are lexical
inventory counts (comma splitting is not a full CSS selector parser).

| File, before | Bytes | Rule blocks | Comma-split selectors | Media queries | `!important` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `app/static/css/base.css` | 985,335 | 5,868 | 7,774 | 130 | 76 |
| `app/static/css/mobile_drawer.css` | 7,446 | 50 | 65 | 2 | 1 |

No exact duplicate property/value declarations were found **within** one
block. Nine groups repeat normalized blocks, totalling approximately 621
bytes of repeated bodies; matching text does not prove redundant cascade
behavior. Examples: `.neoermac-door-context` at 3430/17182, `.shell` at
15094/17805, Sektor tunnel offsets at 21510/22881, Gateway mobile icon/link
rules at 24149/24683. These were kept because media/cascade placement can
matter. The inventory flags 393 class candidates with no direct external
literal match; dynamic `blueprint-*`, state, flash, node and responsive
classes invalidate an automated-purge approach.

The stylesheet contains 4,239 node-prefixed rule blocks, alongside shared
chrome, responsive rules, and historical visual layers. Broad scoping of
`.shell`, `.content`, headings, inputs and high-specificity body/state chains
creates cascade risk. The removed login selectors are the only proven
orphan rules changed in this pass. No global typography/mobile redesign.

Recommended follow-up: map each node's rules plus shared dependencies, extract
in **original cascade order**, load the appropriate node bundle, then compare
computed styles/screenshots for desktop/mobile, drawer, dock, Board View,
disabled, pending and print states. Moving rules alone saves **zero repository
CSS bytes**; it can reduce per-page transfer. Total possible removal is not
honestly measurable from grep candidates. Only the 1,357-byte reduction in
this pass is verified; the 621 repeated-body bytes are a review ceiling, not
promised additional savings. Do not run a blind purge or reorder overrides.

## Ranked remaining findings

All locations refer to the starting commit unless a symbol is provided.
"Risk" distinguishes confirmed structure from unproven exploit/performance impact.

| Priority / classification / severity | Evidence and references | Risk / recommended next action |
| --- | --- | --- |
| P1 FOLLOW-UP / RISKY / Medium | `neoscorpion_learning_vault.py:read_calibration_review` bounds compressed input to 1 MB, then calls unbounded `gzip.decompress`; only capture mode/training eligibility are validated afterward. Routes require settings access; key regex restricts calibration-review paths. | A crafted permitted object can expand far beyond the compressed bound. Not a demonstrated unauthenticated attack. Add an explicit decompressed-byte budget, streaming decompression, schema/checksum validation and oversized/corrupt-object tests in a focused Vault repair. Agree on valid historical payload bounds first. |
| P1 FOLLOW-UP / RISKY / Medium | `neostaffing/routes.py:update_unit` and multiple `_mutate`/holiday handlers flash `str(error.orig or error)` for `IntegrityError`. | Authorized users can see SQL/constraint/input details. Replace with specific sanitized user errors plus server-side diagnostic logging; preserve validation messages and add exception-render tests. No credential leak confirmed here. |
| P1 DB / NEON COST / Medium | `neostaffing/routes.py:change_requests` (420), `staffing_notifications` (442) call `_maintain_change_request_activity` on GET and commit only if changed. `neostaffing_notifications.py:354` materializes overdue notifications; queries all overdue requests, people/leadership and all unit parents (391). | Passive visits can do global maintenance/writes. Semantics are intentional, not dead code. Measure representative query/write counts and move to bounded, transactional on-demand scope only after notification ownership/retention tests. No scheduler/keepalive proposed. |
| P1 DB / NEON COST / Medium | `neostaffing_notifications.py:317` loads pending requests for navigation; leadership resolution uses further per-person queries (332) and unit maps (337). | Work grows with pending requests/people rather than rendered results. Profile realistic isolated data; batch relationships and retain permission filtering before considering pagination. No production counts measured. |
| P1 FOLLOW-UP / RISKY / Medium | Full baseline has 405 failures/subfailures across old UI expectations, security/workflow fixtures, schema expectations and validation contracts. `test_local_launch_navigation`, `test_neoermac_routes`, `test_grandmaster_user_management` are prominent groups. | Green-only cherry-picking would conceal drift. Triage by failure type; align intended contracts with fixtures, separately investigate actual behavioral failures. Do not blindly rewrite assertions or call every failure stale. |
| P2 DB / NEON COST / Low–Medium | `auth/routes.py:_portal_app_access_rows` loops the three-app catalog and queries non-Gateway `PortalAppAccess` individually. NeoGateway uses `ensure_user_app_access`, which maintains canonical membership state. | Small bounded repeated reads, with nontrivial Gateway synchronization. Batch non-Gateway rows only after request-query tests; never replace the canonical Gateway helper with flight/role-style shortcuts. |
| P2 DB / NEON COST / Medium | `auth/routes.py:_apply_permission_rules_from_form` resolves each submitted rule; `neomotherbrain/routes.py` around 4514 resolves review items per row; `alp_import.py` around 135 resolves missions by row ID. | Row-dependent work in administration/import paths. Preload scoped IDs and preserve validation order, ambiguity and transaction isolation. Do not mix with the already-optimized Google live mission source-link path. |
| P2 NETWORK / POLLING / Medium | `neoscorpion_learning_vault.py:_r2_client` constructs a boto3 client with signature config only; no repository-specific connect/read timeout or retry policy is set. | Vault actions inherit SDK defaults, unlike the explicitly bounded Google path. Measure action latency; set a separate bounded Vault policy if approved. Do not change Google cadence/leases or introduce implicit write retries. |
| P2 FOLLOW-UP / RISKY / Medium | `integrations/google_motherbrain.py:current_sort_preview` checks `content_length`, then reads the entire body and validates its size. No app-wide `MAX_CONTENT_LENGTH` was found. Route is disabled by default and authenticated with constant-time token comparison. | Unknown-length requests are checked after allocation. Review server/request streaming limits and add bounded-body tests before changing request contracts. No confirmed external exploit or auth bypass. |
| P2 CSS CLEANUP / Low | Historical grouped selectors and 393 unmatched candidates coexist with dynamic classes; nine normalized repeated-block groups remain. | Unsafe to delete from grep alone. Use the decomposition/computed-style plan above; preserve exact ordering and responsive states. |
| P2 FOLLOW-UP / RISKY / Low | `neostaffing/people.html` hierarchy open/scroll state reads/writes `localStorage` directly; drawer-close handling also exists in `live_updates.js:bindPeopleDrawerClose`. | Storage-denied browsers can interrupt later inline initialization; duplicated close listeners have different detail-drawer semantics. Isolate storage failure and consolidate only with real drawer lifecycle tests, not as blind dead-handler removal. |
| P2 SAFE CONSOLIDATE candidate / Low | AST-unused candidates: `SortDateOperation` in `app/services/neoscorpion.py` and `app/neonodes/neorain/routes.py`; `NEORAIN_OUTBOUND_REFRESH_KEY`/`NEORAIN_INBOUND_REFRESH_KEY` in Rain routes; `PRETREAT_REFRESH_KEY`/`entity_version` in `app/neonodes/neosubzero/routes.py`; `StaffingVacationManagementSelection` in `app/services/neostaffing_vacation_reports.py`; `NeoSubZeroDepartureDeiceEvent` in `app/services/neosubzero_spray.py`; `tail_status_is_hot_for_operation` in `app/neomotherbrain/routes.py`. `RAMP_ORDER` proved to be a live re-export. | Candidates are not deletion proof. Check callers, re-exports, mocks and dynamic lookups individually before removing; only two fully verified imports were removed. |
| P3 FOLLOW-UP / RISKY / Low | `app/static/manifest.webmanifest` retains legacy `neoportal` paths. `base.html` uses registered dynamic `pwa_manifest_by_key`; canonical icon README identifies the static file as unused. | No current template consumer, but historic direct installations/bookmarks cannot be ruled out from repo references. Keep pending an explicit legacy-install retirement decision. Do not edit the authoritative working manifest. |
| P3 KEEP / Low | `images/hero/hero_neopapps*.png`, `neoapps_portal_mobile.png`, transparent cosmic NA pack, old `neorfd_logo1.png`, and older Gateway derivative names have few/no active literal consumers. | Approved masters, historical sources and configurable `DEFAULT_GATEWAY_LOGO`/external references are not safe purge targets. Retain; request an explicit retirement list if storage reduction beyond this pass is desired. |
| P3 KEEP / Low | Hash-equal PWA/in-app/favicon sizes and maskable variants; NeoFont glyphs B–Y lack individual literal references. `generate_neoapps_icons.py`, PWA specs and font tools construct these paths. | Canonical file/size contracts, not duplicate waste. Keep complete generated families and source masters; do not alter approved masking/font geometry. |
| P3 KEEP / Low | `neonodes/neoscorpion/_menu.html` loads `neoscorpion_menu_overlay.js` and is included by Fuel Dispatch, Hanzo, Fueler, History, Settings, Calibration and Truck Manager. | Earlier overlay code is still referenced despite the shared shell. Hidden presentation alone does not prove dead code. Keep; any consolidation belongs in a tested shell task. |
| P3 KEEP / Low | `scripts/bootstrap_database.py`, `database_bootstrap.py`, `schema_sync.py`, narrow compatibility modules, `init_db.py`, font tools and seed/recovery scripts have explicit CLI/test/bootstrap uses. | Low runtime call frequency is not obsolescence. Preserve recovery tooling and the canonical explicit bootstrap path; historical branding audit is clearly dated, not current deployment instructions. |

## Security and dependency results

Application findings: **0 confirmed CRITICAL, 0 confirmed HIGH exploits**;
two Medium HTML-injection paths fixed. Remaining Medium hardening concerns are
ranked above. This does not assert absence of vulnerabilities outside reviewed
paths or deployment configuration.

Reviewed and kept:

- `auth/decorators.py`, `permission_rules.py`, `access_control.py`, node route
  decorators and representative object/operation checks: server-side access
  remains distinct from menu visibility. Broad new cross-gateway IDOR tests
  are a useful follow-up, not evidence of a confirmed IDOR.
- Central CSRF enforcement; token-authenticated Google preview is explicitly
  exempt and preview-only. Unsafe methods are not exempted just for being
  public assets. Authenticated Share QR stays private.
- `app/__init__.py` explicit GET/HEAD public-asset and `/healthz` exemptions,
  session-version invalidation, password-change enforcement, CSP nonce and
  security headers. No broad path/blueprint bypass added.
- SQL text interpolation inspected in schema helpers uses internal schema
  contracts; request IDs normally go through ORM parameters and scope checks.
  No confirmed request-controlled SQL injection found. Redirects inspected
  use existing `url_for`/allowlisted endpoint helpers, not arbitrary next URLs.
- Production secret validation rejects the development key; secure cookies,
  SameSite/HTTPOnly and explicit trusted-proxy configuration remain. Tracked
  private-key markers occurred only in four Google test fixture modules;
  no tracked `.env`, `.pem`, `.key` or credential-JSON filename was found.
  This limited scan is not a full Git-history secret audit.

`python -m pip check` reports no broken installed requirements. No audit
package was installed. Public PyPI per-version vulnerability metadata was
checked for the local environment; that is **not a production SBOM** and
empty metadata is not proof of safety.

| Classification / priority | Evidence / reachability | Next action |
| --- | --- | --- |
| Dependency security FOLLOW-UP / P1 / application severity unconfirmed | Local Pillow 12.2.0 has published advisories, including PCF/BDF resource-exhaustion issues fixed in 12.3.0. Direct repo `PIL` calls are in trusted artwork/font tools and tests, not public uploads; ReportLab is a runtime consumer. | Review all applicable advisories and runtime transitive paths; upgrade/pin in a separately validated dependency task, not an untested hygiene upgrade. [Installed-version metadata](https://pypi.org/pypi/pillow/12.2.0/json). |
| Dependency security FOLLOW-UP / P2 / application severity unconfirmed | Local cryptography 49.0.0 is listed for GHSA-g6cj-pr64-35w5, fixed in 50.0.0. Advisory concerns attacker-controlled PKCS7 EnvelopedData decryption; no `pkcs7_decrypt_*` usage found in repo. | Check deployment-resolved version and actual transitive use; no demonstrated reachable exploit here. [Advisory](https://osv.dev/vulnerability/GHSA-g6cj-pr64-35w5). |
| Dependency tooling FOLLOW-UP / P2 / application severity unconfirmed | Local pip 26.0.1 metadata lists advisories with fixes in later releases. pip is build tooling, not an application request dependency. | Review build-environment update separately. [Metadata](https://pypi.org/pypi/pip/26.0.1/json). |
| Dependency hygiene FOLLOW-UP / P3 / Low | `pypdf` is directly imported only by `test_neostaffing_vacation_reports.py`; Flask ecosystem packages are transitives, OR-Tools is used by optimizers, ReportLab by PDFs/QR, boto3 by Vault. Local environment does not contain boto3 although requirements declares it. | Consider moving test-only requirements to a documented dev set and restoring local optional-provider parity. Do not remove working transitive/runtime requirements on an import-count heuristic. |

No dependency removed/upgraded. No tooling package installed. No credentials,
environment values, or private network/database data were sent to advisory
services.

## DB, network and startup contracts retained

No DB/network inefficiency was automatically changed: **0 query reductions,
0 write reductions, 0 network calls saved claimed**. Inventory counts of
`.all()`, `.count()`, loops or commits are syntax counts, not production query
counts. The meaningful growth paths are listed for scoped measurement above.

Preserved `google_motherbrain_live_missions` source-link-first identity and
batch indexes; `google_motherbrain_sheets` supported HTTP timeout (default 5s),
writer/Rain reuse; `google_motherbrain_live_poll_execution` guarded lease-success
stage before atomic primary commit and separate Rain best-effort boundary.
No polling cadence, lease duration, authority, retry, or process-global
operational cache changed. Existing visibility/inactivity tests protect
`live_updates.js` timers; the only change there is text rendering in Staffing.

Normal PostgreSQL construction stays DB-free; schema compatibility belongs to
`scripts/bootstrap_database.py -> bootstrap_database -> sync_database_schema`.
`/healthz` remains DB-free. Runtime pre-ping/connect timeout and bootstrap-only
lock/statement bounds remain separate. This code-only Neon review deliberately
avoided new keepalives or production profiling that would wake an idle database.

## Verification and metrics

Commands below use `.\.venv\Scripts\python.exe` for `python` and
`C:\Users\kknfi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`
for `node`. Tests use local synthetic fixtures; browser tests were excluded.

Baseline:

```text
python -m pytest -q tests --ignore=tests/browser --tb=no --disable-warnings --junitxml=instance/repo-audit-2026-09-06/baseline.xml
2336 passed; 405 failures/subfailures; 1142 subtests passed; 620480 warnings; 831.25s
```

The 405 failure elements belong to 324 XML testcases; do not sum them as 729
distinct defects. Examples include retired icon/card markup assertions,
302-versus-200 expectations, and startup/schema contracts changed in earlier
commits. Not all have been diagnosed as stale tests. Full failure names and
tracebacks remain in the XML, untouched by cleanup.

Focused checks:

```text
python -m pytest -q tests/test_security_headers.py tests/test_request_access_cache.py tests/test_render_startup.py tests/test_google_motherbrain_live_missions.py tests/test_google_sheets_timeout.py tests/test_neoapps_icon_family.py --tb=short --disable-warnings --junitxml=instance/repo-audit-2026-09-06/focused.xml
81 passed; 209 subtests passed

python -m pytest -q tests/test_db_free_liveness.py tests/test_auth_account_flows.py -k "liveness or static or asset or session or required_password or neobid or qr" --tb=short --disable-warnings --junitxml=instance/repo-audit-2026-09-06/auth-focused.xml
9 passed; 58 deselected

python -m pytest -q tests/test_neostaffing_routes.py -k people --tb=no --disable-warnings --junitxml=instance/repo-audit-2026-09-06/people-focused.xml
23 passed; 17 subtests passed

python -m pytest -q tests/test_google_motherbrain_live_poll_execution.py tests/test_google_motherbrain_live_poll_lease.py tests/test_google_rain_sheets.py --tb=short --disable-warnings --junitxml=instance/repo-audit-2026-09-06/polling-focused.xml
52 passed; 17 subtests passed

node --test tests/js/people_text_rendering.test.js tests/js/live_updates_inactivity.test.js
10 passed
```

All four new text-rendering tests fail when their source reads are redirected
to `git show HEAD:<source>` at the starting commit, and pass against repaired
files. This is a local Node/DOM-seam regression, not browser execution.

The complete existing JS test invocation is PowerShell-expanded:

```powershell
$auditTests = (Get-ChildItem -LiteralPath tests/js -Filter '*.test.js').FullName
& $auditNode --test --test-reporter=tap $auditTests
```

Baseline: **31 passed / 4 failed**. Final: **35 passed / the same 4 failed**.
The pre-existing failures are in `tests/js/neoscorpion_dispatch_workflow.test.js` assertions for
dispatch autosave/compact rows/assignment controls/APU editor; no Scorpion
code changed. Live-update timer/inactivity tests remain green.

`node --check` was run for tracked `.js` files; the `.gs` file was piped to
`node --check` (syntax only). The new test file also executes in Node.
`python -m compileall -q app scripts tools init_db.py run.py` passed.
`python -m pip check` passed. `git diff --check` passed (only ordinary
Windows LF/CRLF checkout warnings, no whitespace errors).

Static version is `20260906-03` for the changed CSS/JS. An exact-text CSS comparison against the starting blob verified that only the
nine identified blocks and two grouped-selector entries disappeared; every
other character of the stylesheet is preserved. All three archive hashes
were independently compared against Git blobs after removal. SHA-256 checks
also confirm all 232 remaining tracked images/glyphs are byte-for-byte unchanged.

No browser screenshots or physical-device checks were performed: Playwright
is not installed in the repository venv, and no browser dependency was added
for this non-redesign cleanup. Existing route tests exercise representative
Login, Portal, Gateway, MotherBrain, Staffing, Ermac, Sektor, Scorpion, Rain
and SubZero pages, with pre-existing failures explicitly retained. This does
not establish production or visual/browser verification.

### Measured size changes

Sizes use Git blob bytes (LF-normalized text, original binary bytes), not
working-tree CRLF expansion, Git-history pack size, gzip transfer size, or
installed dependencies. Images include PNG/JPEG/ICO and the 26 SVG glyphs.
Source totals include tests/scripts where applicable; JS includes `.gs`.

| Metric | Before | After | Delta |
| --- | ---: | ---: | ---: |
| Tracked files | 862 | 861 | -1 (3 images removed, report + regression test added) |
| Python files / bytes / LOC | 416 / 7,287,014 / 191,045 | 416 / 7,286,961 / 191,043 | -53 bytes / -2 lines |
| CSS bytes / LOC | 992,781 / 32,530 | 991,424 / 32,462 | -1,357 bytes / -68 lines |
| JS/GS files / bytes / LOC | 26 / 261,439 / 6,626 | 27 / 267,524 / 6,766 | +6,085 bytes / +140 lines (tests + text-safe DOM) |
| Static images / bytes | 235 / 72,109,292 | 232 / 70,848,742 | -3 / -1,260,550 bytes |
| Dead rule blocks removed | 0 | 9 | 3 class families; 2 additional selector-list entries |
| Dead code bindings removed | 0 | 2 | imports only; no live modules/routes deleted |
| DB/network inefficiencies changed | 0 | 0 | no unmeasured savings claimed |

Asset archival reduces the checkout, not live-page transfer (the images had
no live references). Git history still retains those blobs. Audit/report and
regression-test additions partially offset the net checkout saving.

### Final regression comparison

```text
python -m pytest -q tests --ignore=tests/browser --tb=no --disable-warnings --junitxml=instance/repo-audit-2026-09-06/final.xml
2336 passed; 405 failures/subfailures; 1142 subtests passed; 620480 warnings; 834.96s
```

XML comparison by testcase identity, failure/error category, exception type
and multiplicity shows **zero new/changed failures and zero removed baseline
failures**. Both full runs exit nonzero because of the same pre-existing
failures. Focused Python runs: **165 passed + 243 subtests passed**. All four
new JavaScript regressions pass; the complete JS run retains only its four
pre-existing failures. The temporary re-export collection error was corrected
before the full final run; no collection errors remain.

Final cleanup comprises 12 files: `.gitignore`, two Python import cleanups,
`app/config.py` cache version, `base.css`, `live_updates.js`, Staffing People
template, three archived/deleted button images, this report and the new JS
regression file. No approved artwork or unrelated work is included.

Total tracked Git-blob bytes: **81,856,351 -> 80,629,080**; net reduction:
**1,227,271 bytes** (including this report and regression tests). This is
checkout content, not compressed Git-history storage.
