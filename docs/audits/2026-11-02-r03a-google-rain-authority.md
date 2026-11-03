# R03 Remediation A — verified Google Rain field authority

Starting SHA: `6ac5756141b5fc0116d18a93ca1dff3f3a6618e1`.
Repository: `Z:\DevProj\NeoApps`, main. The checkout had no tracked edits and
the six existing unrelated untracked artwork/prototype items were preserved.
Fetched origin/main matched the starting SHA.

## Decision

**The UNKNOWN Ramp Load assertion was outdated against the shipped explicit
field-authority contract. The described cloud source-only patch was not applied.**

UNKNOWN is unresolved provenance. It is neither proof of a manual edit nor an
authorization grant to Google. Google authority comes from the configured
integration and its validated, operation-scoped import path. For the load/eLMAC
projections, an actual operator edit is protected by a durable, active
`NeoRainMissionOverride`, not by a timestamp's legacy source label alone.

Under that existing contract, a valid ordinary Google Rain import may update
UNKNOWN 07:00 to Google Rain 07:30 **when no active field override exists**.
A real authorized Ramp Load edit to 07:00 remains 07:00 against conflicting
07:30 imports and withdrawals; its source remains `neorain`. The same applies
to eLMAC and Crew Load. Explicit clear remains an active protected NULL.

No production authority policy was changed to make the test pass.

## Evidence and the rejected partial patch

- `app/neonodes/neorain/routes.py`'s `/outbound/milestone` and `/inbound/block-in`
  routes now delegate to `handle_override()`, as does `/neorain/override`.
- `override_routes.py::handle_override()` checks permissions, current gateway/
  sort/mission, optimistic version and mission row locks before invoking
  `neorain_overrides.py::edit_override()`.
- `edit_override()` records explicit presence, actor, generation, source,
  upstream snapshot and audit. `observe_import()` captures the actual upstream
  fact separately and shields the canonical field while the override is active.
- Reset makes the override inactive and restores upstream synchronization.
  `docs/audits/2026-10-14-neorain-release.md` explicitly states that ordinary
  imports resume after removing an override and that non-overridden fields
  continue normal import updates.
- Commit `2c05915a` deliberately introduced that adapter behavior. The older
  `_neorain_may_mutate_timestamp()` compatibility helper still has conservative
  source-label rules, but it is not the supported current HTTP editor. Its
  definition/import is not evidence that a current edit lacks an override row.

A counterfactual probe reconstructed the *described load-field ownership rule*
using the pre-explicit-override helper. It used real local edit/reset routes:

1. Store synthetic UNKNOWN 07:00.
2. Authorised edit to 07:10 creates a real override.
3. Reset before any intervening Google observation restores UNKNOWN 07:00.
4. Import valid Google 07:30.

| Policy | Result after reset and import |
|---|---|
| Current explicit-authority contract | 07:30 / `google_rain` |
| Proposed source-only ownership rule | 07:00 / `unknown`, synchronization stuck |

The cloud files themselves were not available in this Windows checkout. This
was a reconstruction of the handoff's described policy, not a claim to have
reviewed its unavailable final file bytes. No cloud commits or dependencies
were imported. Its intermediate “66 passed” result is not used as validation.

## Field-specific boundaries retained

| Field/context | Preserved rule |
|---|---|
| eLMAC, Ramp Load, Crew Load | Active explicit override protects the value/source; an unoverridden projection follows legitimate Rain revisions and withdrawals. Source labels alone do not create overrides. |
| Native Block-Out | R07's separate native-source protection remains before generic field handling, even without an override row. |
| ALP Block-Out | R08's native/Google precedence and scoped, locked import behavior remain unchanged. |
| Explicit Block-In override | Protected against primary Google ARR updates; actual incoming upstream values remain recorded separately. |
| Unoverridden primary-Google arrival Block-In | Existing arrival-correction contract remains unchanged. The existing `test_unloaded_never_downgrades_but_arrival_corrects_actual` explicitly allows Google ARR to correct a source-labelled value; this release does not extend the Block-Out exemption to Block-In. |
| Explicit reset | Returns control to upstream; ordinary imported flags cannot reset or grant override authority. |
| Missing row fields | Stay missing; they are not converted into withdrawals or invented milestone evidence. |

The original test's Block-Out timestamp/source preservation and absent-eLMAC
assertions remain. Its obsolete blanket load-source assertions were replaced
with the verified projection expectations and new tests of the **actual**
authorized editor, clears, resets, permissions and concurrent imports.

## Narrow application correction

One separate defect was confirmed: an already-empty field with source UNKNOWN
was compared against `google_rain` before deciding whether an import changed it.
The adapter consequently reported an unchanged empty fact as changed.

The correction compares against the source that would actually be stored:
`google_rain` for a value, `unknown` for NULL. No precedence rule was removed.

| Repeated empty eLMAC import | `changed_fields` | SQL UPDATEs |
|---|---|---:|
| Starting SHA | `['elmac_completed_at_utc']` | 0 |
| Corrected | `[]` | 0 |

This is a reporting/idempotency correction. There is **no claimed database-write
saving** in that probe: both paths already emitted zero UPDATEs. Tests verify
the same no-op contract for all four covered timestamps and unchanged versions.

## Validation

Existing Windows `.venv`: Python 3.13.14, pytest 9.1.1, Playwright 1.63.0.
No dependency installation, WSL benchmark, production provider test or hosting
change was performed. Python sockets were restricted to loopback and browser
integrations were blocked/mocked. Fixtures and PostgreSQL schemas were synthetic.

| Group | Result |
|---|---|
| Baseline original Rain module + R07 canonical tests | 1 failed, 31 passed, 7 passing subtests; exact UNKNOWN 07:00 → 07:30 failure reproduced. |
| Final focused Python, including integration-mode transitions, overrides, R07/R08 and separate primary Block-In behavior | **73 passed**, 45 passing subtests, 12,122 warnings, 23.64 s. |
| Disposable PostgreSQL authority/override/R07/R08 group | **52 unique cases passed**, 46 passing subtests, 13,649 warnings, 122.87 s. |
| Strengthened PostgreSQL upstream-applied/no-op assertions | **2 passed** on rerun, 4 passing subtests, 739 warnings, 5.60 s; these overlap the 52-case group and are not two additional unique cases. |
| Final Chromium/WebKit load edit/reset + canonical Block-Out workflows | **4 passed**, 907 warnings, 46.60 s. |
| Focused override/CSRF/template JavaScript | **32 passed**, 0 failed/skipped. |
| Compilation and whitespace | Changed Python files compile; `git diff --check` passes. |

New PostgreSQL concurrency tests use independent connections and inspect
`pg_stat_activity` to prove the poll waits on the canonical edit's mission lock.
After commit, all three explicit load edits retain 07:00/`neorain`; actual
Google 07:30 facts are captured in upstream snapshots. A changed physical-tail
identity skips the stale import. Stale operator versions receive HTTP 409 and
cannot partially replace the accepted imported fact. Watchers receive 403.

Browsers exercised desktop Chromium at 1920×900 and mobile WebKit at 390px:
UNKNOWN projection update, actual inline manual edit, conflicting poll, reset,
and resumed upstream revisions. The initial new WebKit test omitted opening
the existing DETAILS drawer and failed to find the hidden control. The fixture
was corrected to use that existing operator action; no UI implementation was
changed. A mistyped existing test class selector caused one zero-test collection
attempt; the corrected complete selection is the 73-pass result above.

Warnings were retained, chiefly existing datetime/legacy ORM deprecations.
The broader R03 baseline has not been rerun or declared green. This resolves
this case's authority ambiguity; it does not close the other R03 findings.

## Preservation and release

No historical production milestone was repaired or rewritten. No Google Sheets,
credentials, models, FROST logic, archives, permissions, worker configuration,
schema or hosting settings were changed. The local PostgreSQL server was stopped
after tests; its synthetic files were preserved.

Changed files:

- `app/services/google_rain_live_milestones.py`
- `tests/test_google_rain_live_milestones.py`
- `tests/test_google_rain_load_authority.py`
- `tests/test_google_rain_load_authority_postgres.py`
- `tests/browser/test_google_rain_load_authority.py`
- This report and its [evidence register](2026-11-02-r03a-google-rain-authority-evidence.json).

The release commit is the commit containing this report. Exact final SHA,
Render deployment ID, bootstrap and read-only LIVE checks are verified after
AutoDeploy and included in the completion response.
