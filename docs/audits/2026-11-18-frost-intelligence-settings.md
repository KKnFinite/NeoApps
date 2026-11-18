# FROST Intelligence controls

Base: `dc735b6148697bfc76733ce94388f68483e86f63`, latest main rechecked before release.

## Implemented

- Three per-gateway NeoSubZero Settings switches default OFF: Learn from Live Operations, Automatic Retraining, Operational Predictions & Alerts. Existing weather acquisition and eight frozen historical-model research predictions do not depend on these switches.
- Reuses `FrostHistoryTask`'s existing compact, unique gateway/kind/identity/version control records. No table, migration, new Render worker, recurring poll, or infrastructure change.
- GET/API reads never initialize or write control records. Saving uses current revision, the existing administrative/account locks, a gateway lock, and an atomic immutable `AccountSecurityEvent` containing actor, gateway, previous/new values and UTC timestamp. Duplicate unchanged saves do not create audits. Stale writes reject; invalid activation rolls back the complete form.
- Only active, email-verified platform Grandmasters may save. Uses `account_security.platform_grandmaster`, the active scoped platform authority adapter and fresh locked reauthorization. Node Masters/Grandmasters and NeoApps Master + application Grandmaster cannot obtain this authority. Settings-view users see disabled switches. Both HTML and JSON mutations enforce the same service guard and existing CSRF protection.
- Operational activation requires explicit confirmation. It currently rejects: the installed runtime and existing registry approvals are research treatment-cohort contracts, not independently validated operational physical-frost models. Historical approval, metadata claims, research scores and toggles cannot bypass that scientific boundary. The operational inference entry point fails closed before weather/model loading or scoring; research inference remains separate.

## Learning and training boundaries

`frost_inspections` and explicit no-treatment capture now seal the capture-time control epoch. OFF captures, legacy evidence lacking that provenance, existing sorts predating enablement, uncertain results and unverified nightly treatment/no-spray records cannot become live training truth. An eligible future observation requires a new-sort explicit physical inspection, actor, source attribution and recorded weather evidence after the opt-in epoch. Corrections retain original eligibility provenance, including permanent testing exclusions. Physical observations, Cryotech reasons, audit history and original archive objects remain intact.

The offline prospective consumer preserves UNKNOWN for unverified nightly spray reasons and no-spray records. A verified inspection's physical result remains separate from treatment-reason research. Enabling a switch does not change any frozen model, classify unchecked observations as frost, promote a model or establish prediction accuracy.

Training remains workstation-only, preserving the existing explicit prohibition on Render/web-request fitting. `scripts.frost_retrain` is a bounded `--once` background entry point for the existing workstation scheduling mechanism. It reuses the canonical historical preparation/fitter and temporal/quality gates; requires fresh authenticated control and matching dataset gateway; serializes local execution; refuses existing candidate paths; and checks the same control revision before work and candidate publication. Disabling/changing control prevents new work and discards an in-flight result when the final check detects it. Already running numerical work is allowed to finish safely; no process is killed. Candidates remain unapproved and cannot replace the eight frozen models automatically.

No workstation schedule is installed by this release. Enabling retraining permits a configured controlled workstation runner; it does not fit models inside Render or claim that an unattended machine is available. Manual historical research remains independent. Example, using an existing verified private historical dataset and a unique candidate version:

```powershell
python -m scripts.frost_retrain --local --once --origin https://neoapps.onrender.com --email YOUR_ACCOUNT --directory instance/frost-local/verified-dataset --output instance/frost-local/automatic-candidates --version UNIQUE_RESEARCH_VERSION
```

Authentication reuses the existing `Remote` client. No password is saved or placed in command-line arguments. Turning OFF does not delete historical evidence or candidates. An independent physical-frost population, compatible operational runtime, validation and explicit operational model commissioning are still required before operational alerts can be enabled.

## Focused verification

- **44 passing Python/SQLite/unit cases**: policy persistence, read-only GET, no-op/stale/forged requests, scoped platform authority, node/application denial, fresh revocation, CSRF, rollback, future versus testing evidence, correction provenance, candidate-only worker and disabling, eight frozen artifacts/H0/H1/H3/H6, inspection audit/outbox behavior, research hourly scoring and archival retry isolation. `instance/frost-intelligence-verified.log` (24.777 s).
- **3 passing PostgreSQL independent-transaction cases**: concurrent saves have one winner/one audit; revocation while a writer waits rejects after locking; injected failure rolls back both control and audit; bulk audit mutation is rejected. Isolated loopback schema is preserved. `instance/frost-intelligence-postgres.log` (34.012 s).
- **Chromium and WebKit**: 1366px desktop and 390px mobile, default OFF states, genuine save, rejected operational activation without partial changes, cross-user persisted state and Master read-only controls. `instance/frost-intelligence-browser.log`; screenshots `instance/browser-evidence/frost-intelligence/`.
- Python compilation and `git diff --check`. No JavaScript changed. Existing deprecation/resource warnings remain visible. One injected archive failure verifies safe retry, not a production error. Initial fixture errors (detached test user, missing synthetic activation audit and duplicate fixture entitlement) and obsolete pre-launch label expectations were corrected without weakening authority or scientific gates.

No production settings toggles, training, inspections, operational records or account grants are changed during verification. Existing unrelated files, jobs and synthetic database directories remain preserved. Exact release SHA, AutoDeploy ID, startup, health and authenticated OFF-state verification are recorded in the completion response.
