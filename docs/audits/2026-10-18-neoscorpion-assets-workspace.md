# Manage Tonight's Assets: integrated management workspace

Starting main/origin main: `266e2424d418e5f2e5836359cf8441e721a5e1ad`.
UI/presentation and response-contract change only. No schema, fueling, inventory
posting, status-calculation, SPEAR or PROF service changes.

## Placement and capability inventory

| Workspace | Existing capabilities retained |
| --- | --- |
| Fuel Islands | Current count and current-sort configuration |
| Nightly Fuelers | Roster, operational nickname/version, add and remove |
| Nightly Trucks | Canonical capacity/current/immutable opening fuel, physical activity/status, current-fuel correction, pending fuel approvals, inventory verification, TOP OFF/RETURN, confirmed sump verification, serviceability, add/remove nightly asset |
| Driver controls per truck | Current verified/manual/UNKNOWN identity; manual name; eligible employee and approved vendor accounts; disconnect; explicit conflicting-reassignment checkbox and reason; existing expected-driver/current-truck validation |
| Truck claims | Pending verified driver identity, truck, proposed opening gallons versus existing balance, approve/deny and explicit conflict override; approval uses the existing service and never resets configured inventory |
| Secondary administration | Invitations/resend, nightly approve/deny/revoke, enable/disable account, registration/email status, audit history |

All normal truck MANAGE links open the exact integrated truck inside the workspace,
without navigation. The old protected driver page remains a compatibility fallback;
normal Dispatch no longer requires it. Data comes from the same shared gateway/sort
management context, with no new account or truck registry.

Controls use the existing authenticated POST routes and services. The workspace
opts into JSON success/errors after commit; native legacy contracts are preserved.
Current-sort workspace asset requests reject a stale operation ID. Invitation email
failure still rolls back account creation. Invitations publish the existing nightly
revision so another Dispatcher can see new account administration without a poll
performing writes.

## Layout and refresh

Compact side-by-side islands/Fuelers and full-width truck rows. Small body typography,
27px controls, aligned fuel and driver inputs, no card-within-card layout. Claims are
immediately visible; account and audit administration is secondary.

Management sits outside the banner hidden by Board View. Its open Board workspace is
bounded to 50vh with internal scrolling; MANAGE reveals and focuses the intended truck.
Live replacement retains workspace/disclosure expansion and internal scroll.
Dirty/focused edits are protected; independent clean truck/account/claim regions and
read-only balances/driver labels can update while another draft remains.
Resolved claims disappear without waiting for an unrelated draft to be saved.

Mobile Dispatch retains its established read-only restriction: island/Fueler/truck
roster facts are readable, but no administrative form/input/action is exposed.

## Validation evidence

- 44 focused Python tests passed: integrated action inventory, no nested forms,
  authorization, sort/version guards, invitation failure, driver identity precedence,
  inventory epochs, legacy asset routes and preserved DETAILS forms.
- 7 real PostgreSQL tests passed in disposable loopback schemas: concurrent inventory
  versions, duplicate claim approval, concurrent single-use invitation acceptance,
  competing drivers, driver conflict audit/uniqueness and repeatable bootstrap.
- 15 JavaScript tests passed: independent collapses/sort, completion scroll, workspace
  internal scroll/disclosure persistence, critical completion under drafts, stale
  responses and vendor refresh.
- Two browser workflows passed: the integrated workspace in Chromium and WebKit,
  and the existing Dispatcher COMPLETE scroll workflow. Actual isolated UI clicks
  cover islands, Fueler nickname/add, nightly truck setup, manual and verified driver
  assignment/disconnection, TOP OFF/RETURN, OOS/return to available, invitation and
  nightly approval, vendor claim and Dispatcher approval.
- Vendor claim appeared while another truck's manual-name draft stayed focused and
  unchanged; approval removed the claim, created exactly one nightly asset with
  Starting/Current 4000, and retained the draft. No full-page Dispatcher navigation.
- Desktop 1920/1440/1024 and mobile 390/375: no page horizontal overflow; mobile
  controls hidden; actual Board View MANAGE navigation and bounded scroll passed.
- Compileall, JS syntax and git diff-check passed. Browser images/logs are local
  under `instance/`, not committed. All mail and operational fixtures were isolated;
  no production fueling transaction or real contractor invitation was created.

## Separately reproduced production-baseline failures

Two unchanged legacy service tests fail both in the working tree and in a separate
export of the starting production commit:

- `test_top_off_requires_dedicated_valid_completion`: expects Starting Gallons to
  become 1800 after return; current immutable opening snapshot correctly stays 1200.
- `test_truck_selection_validation_and_atomicity`: an older initial Starting/Current
  validation expectation differs from the already-deployed simplified setup behavior.

Neither assertion was weakened and neither fuel service was changed for this UI
task. Evidence: `instance/assets-baseline-final.log` from the isolated baseline export.
An initial PostgreSQL invocation selected an unavailable psycopg v3 driver; rerunning
with the repository's installed psycopg2 driver passed all seven selected checks.
