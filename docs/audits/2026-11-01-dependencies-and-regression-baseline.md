# Task 15: dependency security and regression baseline

Starting SHA: `f1c71c27f02f7e56493b931a716f4a52d7c1ab04` (fetched main,
already fast-forwarded). The six pre-existing untracked artwork/prototype/audit
items were excluded from all edits, test manifests and staging.

## Dependency findings and corrections

- `Werkzeug==3.1.8` becomes `Werkzeug==3.1.9`. The maintainer advisory
  [GHSA-g6x2-hccm-hh4m](https://github.com/pallets/werkzeug/security/advisories/GHSA-g6x2-hccm-hh4m)
  describes Windows/NTFS device names with an empty ADS marker. Linux Render is
  outside that specific exploitation condition; Windows development still needs
  the fix. Flask 3.1.3's declared Werkzeug requirement accepts 3.1.9.
- `pypdf>=5,<7` becomes `pypdf>=6.21.4,<7`. The original memory advisories
  [strict dictionary parsing](https://github.com/py-pdf/pypdf/security/advisories/GHSA-4vqv-7265-gm2w),
  [xref streams](https://github.com/py-pdf/pypdf/security/advisories/GHSA-x3rh-gjgm-fmqc)
  and [encrypted page counts](https://github.com/py-pdf/pypdf/security/advisories/GHSA-62m5-6wh8-28x7)
  were fixed in 6.21.3. The scan also found 6.21.4 fixes for
  [page-tree memory](https://github.com/py-pdf/pypdf/security/advisories/GHSA-cggf-277g-3q54),
  [trailer-search loops](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5849-834g-hvw5)
  and [dictionary-cloning loops](https://github.com/py-pdf/pypdf/security/advisories/GHSA-mfr5-77xh-9j7h).
  Accordingly the floor excludes 6.21.3 too. Both upstream releases and their
  PyPI availability were verified. Render already resolved pypdf 6.21.4 before
  this task; this prevents an affected older resolution, rather than claiming
  that the prior production pypdf install was 6.16.2.
- `Pillow>=12.3.0` becomes `Pillow>=13.0.0,<14`. The advisory scan found 39
  entries fixed in 13.0.0, including
  [raw-decoder out-of-bounds reads](https://github.com/python-pillow/Pillow/security/advisories/GHSA-4558-3h8r-v8xp)
  and [in-memory TIFF use-after-free](https://github.com/python-pillow/Pillow/security/advisories/GHSA-4rr5-89j8-mwwm).
  Render was already running 13.0.0, so this prevents an affected older install
  rather than introducing a new major runtime upgrade. PNG/JPEG/WebP/TIFF and
  PDF image embedding are explicitly tested with 13.0.0.
- Only these three production security constraints changed. The Windows validation
  environment had older packages than the production build. Its affected
  Werkzeug, pypdf, cryptography, Pillow, certifi, oauthlib, pyasn1 and urllib3
  were aligned with patched production versions. Local-only brotli and pip were
  patched too. Missing local openpyxl was installed from the already-declared
  runtime requirement. Research model artifacts/datasets were not changed.
- `tests/requirements-dev.txt` makes pytest, Playwright and the advisory tool
  reproducible outside Render's runtime install. pip-audit itself ran in a
  separate ignored environment, not the web application's production imports.

Exact package inventories and scan summaries are in
`2026-11-01-dependency-inventory.json`. The starting Linux list was obtained from
the successful Render installation log, not inferred from the owner's Windows
environment. The target Linux list changes only Werkzeug. A fresh Windows
runtime resolution and the full local validation inventory are also recorded.

pip-audit 2.10.1 queried the PyPI advisory service with every direct/transitive
package explicitly enumerated: starting production 50 packages, one known
Werkzeug advisory; target production 50 packages, zero known matches; resolved
Windows runtime 51 packages, zero known matches. Local validation moved from
115 advisory entries in ten packages to zero known matches across 72 packages.
None of those inventories had skipped packages. Advisory counts do not establish
exploitability. This is not a claim that all dependencies are vulnerability-free.
OS/browser/build-toolchain vulnerabilities and future unconstrained resolutions
are outside this package scan.

## Reproduced failures and contract-based classification

The pristine starting application/test source reproduced 17 refresh failures or
subfailures (29 passes) in the targeted Python group and 13 JavaScript failures
(377 passes, including one script-load failure). Broader subsequent runs exposed
the additional rows below. Test failures were investigated against the deployed
contracts; they were not dismissed because of their age.

| Area | Classification and evidence | Correction preserving the original intent |
| --- | --- | --- |
| Four inline JS harnesses | Broken pre-CSP harness: bare `<script>` matching returned no match or sliced Jinja as JavaScript | Shared test-only extractor selects one owned controller and requires its explicit `csp_nonce()` attribute. Three tests prove rejection of missing nonce, missing controller and ambiguity. |
| Authentication enumeration response equality | Outdated expectation: fresh response nonces intentionally differ | Normalize only the nonce attribute; compare all remaining response bytes and status. Assert raw login bodies still differ. |
| NeoErmac live identity | Broken legacy fixture: destination strings and unsigned/destination-owned originals no longer identify a flight | Seed canonical Master Schedule IDs, link the exact departure mission, sign the real slot value, and preserve same-slot conflicts, independent slots, retired IDs, no writes and tail presence checks. |
| NeoErmac static refresh | Outdated implementation-location assertion | Assert the template references its exact extracted controller, then inspect that controller for shared cadence/inactivity behavior. |
| Lifecycle collection/clock | Obsolete removed private-helper import and incomplete clock mocking | Use the current flight-keyed helper. Patch both canonical gateway and lifecycle clocks; retain current/historical operation isolation and a bounded eight-read budget for the two resolver paths. |
| Scorpion refresh | Outdated JSON/opening-tag expectations | Assert the actual tail-note revision fields and parse section attributes instead of depending on exact attribute order. |
| NeoRain milestone/mirror tests | Outdated pre-override transaction contract | Preserve all twelve parent test cases' security/consistency goals using current explicit overrides: canonical commit before guarded mirror attempt, no uncertified writes, saved Neo data survives provider failure, no retry/compensation, current-sort/version/role validation and canonical late-summary calculations. |
| Rain handoff | Incomplete authority fixture | Create explicit override evidence; a bare `manual` source string is not sufficient under the deployed override-presence contract. Foreign intentional values and upstream-only fields remain separately asserted. |
| MotherBrain HOT to SPARE | Outdated expectation | HOT creates a canonical departure. Assert SPARE is rejected, HOT and the exact departure are retained, and the reason is displayed; never delete a mission merely to change a status label. |
| Google poll health | Broken deletion fixture | Retained Rain report children forbid deleting their operation. Test a missing eligible operation one week later, retaining original history and asserting no new operation is created. |
| Mirror-mode Google polling | Outdated mode expectation | Neo-only suppresses upstream reads. Mirror mode still observes upstream/reset provenance. Assert the actual mode-specific read/application contract, without adding a production Google writer. |
| NeoSektor attendance | Actual empty-state application regression plus obsolete fixture seams | A Grandmaster may have no attendance areas. Use `next(..., None)` to avoid HTTP 500. Tests supply the current authority seam, and the sanitized database-error fixture reaches its intended error path with an explicit synthetic editable scope. |
| SQL budgets and commit counts | Outdated cost baseline and broken transaction instrumentation | Count the known single vendor-isolation read; initial pages also require a joined authorization projection. Assert exact new totals and those query identities, keeping every before/after savings delta, payload equivalence and no-write assertion. Count only outer commits, not provisioning SAVEPOINT releases. |
| Portal/FROST/font assets and Stats navigation | Outdated canonical asset/menu expectations | Require exact current paths and cache-version parameters. Verify one FROST CSS and JS resource independently rather than counting a shared old suffix. Preserve Stats access and mobile navigation. |
| Staffing matrix CSS | Outdated declaration-order/overflow text | Assert the actual scrolling class properties, grid geometry, mobile breakpoint, minimum width and tap targets. No application CSS changed. |
| Fuel-event query coverage | Outdated grouping of three distinct evidence reads | Require exactly one compact cycle-total query, plus the separate recency/resource reads, with exactly three event reads at both two and twelve aircraft. Report reads remain constant. |
| CSRF browser assertion | Flaky harness race confirmed by the combined-run traceback: response headers arrived before the meta token was updated | Keep the same token-change assertion but auto-wait for the DOM condition. Preserve all native/AJAX/dynamic-form, draft and exactly-once checks. Browser report navigation/request waits also receive finite test-only deadlines. |

The only operational application code change is the empty-attendance fallback.
No fuel/deice/staffing/Google authority rules, aircraft identity, permissions,
FROST models, scheduling, archive formats, schema or hosting settings changed.

## Validation method and limitations

All functional tests use synthetic databases and mocked external transports.
The broad Python runs also installed a process-only socket guard refusing
non-loopback connections; no test emails, Google writes or R2 writes were sent to
production. PostgreSQL uses disposable schemas on loopback only, 16 MiB shared buffers and
20 maximum connections. The first mapped-drive run was interrupted after slow
DDL setup. The completed validation uses a fresh local-temp cluster; production
and existing operational databases were never used. Read-only py-spy stacks
identified the DDL wait; the debugger was installed only in the ignored tool
environment. The report browser fixture likewise uses a local TemporaryDirectory
while preserving a shared file for independent connections. No WSL benchmarks,
multi-user load tests or production stress traffic were run.

The first all-tracked-files pytest collection was blocked by the obsolete helper
import above. A continued all-files baseline attempt was stopped after partial
progress; it is not reported as a completed full-suite run. Validation instead
uses broad security/affected-node groups, the repository's curated 300-test
Scorpion suite (which avoids rediscovering imported fixture classes), focused
PostgreSQL classes and real Chromium/WebKit workflows. Files from the unrelated
untracked prototype were excluded.

Final completed runs (groups overlap; these are execution counts, not a claim of unique repository-wide coverage):

| Group | Result |
| --- | --- |
| Broad Python security/affected-node group | 691 passed, 880 subtests passed; four PostgreSQL-only cases initially skipped |
| Curated NeoScorpion core | 300 passed |
| FROST/SubZero/parking operational group | 170 passed; eighteen PostgreSQL-only cases initially skipped |
| PDF/report compatibility | 15 passed, including imported fixture coverage |
| Final dependency floor/image/PDF checks | Five passed and eleven subtests passed after the final Pillow/pypdf floors |
| Disposable PostgreSQL | 160 passed, zero skipped, including all twenty-two optional cases above |
| JavaScript | 396 passed, zero skipped; 144 source/test files passed syntax checks |
| Chromium/WebKit | Thirteen unique top-level cases passed across the combined suite and affected final reruns |
| Entire tracked non-browser Python collection | 5,554 collected with no errors; not all executed |
| Compilation, pip check, diff check | Passed |

The initial thirteen-case browser run had one confirmed CSRF assertion race;
its corrected case passed with CSP enforced. A five-case replay then encountered
one Chromium mobile redirect `net::ERR_FAILED` in the unchanged staffing fixture.
Both staffing engines passed unchanged in a clean two-case replay. The cause of
that one navigation failure remains unconfirmed and is retained as a limitation,
not dismissed as a fixed application bug. The report/PDF pair also passed after
its test database moved to local temporary storage. No test was deleted or
broadly skipped to obtain these results.

Both local test servers were stopped. Automatic safety review blocked removal
of the owned local-temp PostgreSQL directory, so its synthetic contents remain
on disk. This does not retain a running service or any production data.

Existing datetime.utcnow, legacy Query.get and fixture SQLite cleanup warnings
remain visible. No blanket warning filters, test deletions, broad skips or
production configuration changes were introduced. Full Linux runtime tests and
all 4,681 declared repository tests were not claimed; remaining unexecuted paths
are outside the measured coverage. Existing provider credential/certification
limitations remain in force.

## Changed files

- app/neonodes/neosektor/routes.py
- docs/audits/2026-11-01-dependencies-and-regression-baseline.md
- docs/audits/2026-11-01-dependency-inventory.json
- requirements.txt
- tests/browser/test_csrf_renewal.py
- tests/browser/test_neoscorpion_report_actions.py
- tests/js/flight_api_auto_poll.test.js
- tests/js/neosektor_tunnel_overrides.test.js
- tests/js/people_text_rendering.test.js
- tests/js/sort_timeline_clock.test.js
- tests/js/template_scripts.cjs
- tests/js/template_scripts.test.cjs
- tests/neoermac_lineup_forms.py
- tests/requirements-dev.txt
- tests/test_auth_account_flows.py
- tests/test_dependency_security.py
- tests/test_frost_vault.py
- tests/test_google_motherbrain_live_poll_execution.py
- tests/test_google_motherbrain_live_poll_health.py
- tests/test_google_rain_integration_mode_transition.py
- tests/test_live_screen_refresh.py
- tests/test_motherbrain_routes.py
- tests/test_neoermac_live_integrity.py
- tests/test_neorain_outbound_mutation_endpoint.py
- tests/test_neoscorpion_live_refresh.py
- tests/test_neoscorpion_sort_fuel_totals.py
- tests/test_neosektor_routes.py
- tests/test_neostaffing_shift_authority.py
- tests/test_operation_lifecycle.py

Production AutoDeploy and read-only verification are reported separately in the
completion response, after the final pushed SHA is observed LIVE.
