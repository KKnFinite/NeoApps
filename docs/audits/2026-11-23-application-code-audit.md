# NeoApps application code and efficiency

November 23, 2026. This is the application-code section of [issue #29](https://github.com/KKnFinite/NeoApps/issues/29). The starting revision was `4336f5f9709ded3375b35f7c520a0e0009437b24`. Measurements below use synthetic operations. Hosted billing and production traffic are separate measures.

## Findings and disposition

| Severity | Location | Reproduction and operational effect | Correction and verification |
| --- | --- | --- | --- |
| P1 | `app/static/js/live_updates.js`, `submitLiveForm` | A Planning tail-swap form received a success HTTP status carrying HTML. It marked the form confirmed and cleared the dirty flag without a confirmed database write. An empty JSON object was also sufficient. | Require explicit JSON success from the action with no redirect. Retain the draft on failure. Distinguish a committed save followed by a failed refresh. Verified with real forms in Chromium and WebKit on disposable PostgreSQL, plus malformed responses in JavaScript. [PR #41](https://github.com/KKnFinite/NeoApps/pull/41) |
| P1 | Planning live template and the desktop/mobile mission-row templates | A historical cancellation succeeded but left CANCEL on screen. A parking change on a different mission reached the browser without changing that mission's timestamp, so it kept displaying the old position. | An explicit action reconciles after any earlier in-flight read. The display version now includes related state, flight labels and the available tail choices. The optimistic write token still comes from the mission. Tested cancel, restore, parking change, stale writes and drafts. [PR #41](https://github.com/KKnFinite/NeoApps/pull/41) |
| P2 | `app/neomotherbrain/routes.py`, Planning fragments and `reconcileRows` | Each departure's mobile selector repeated the entire tail list in a changed-state response. At 156 missions the payload was 1.46 MB. Initial pages also fetched individual permission-rule definitions for each navigation and action check. | Current clients receive one set of choices. The browser restores native select options for changed and new rows. Legacy clients and initial page loads still receive complete controls. Full-page rule definitions use the existing per-request bulk loader. Server-side authorization checks continue for each action. [PR #43](https://github.com/KKnFinite/NeoApps/pull/43) |
| P2 | `app/neonodes/neosubzero/__init__.py` | Importing the departure deicing service first in a fresh process registered routes that imported the partially initialized service. The RADIO PostgreSQL suite failed to load. Normal HTTP startup worked in the baseline fixture. | Register the HTTP routes when the application factory assembles the blueprint. Cold import, RADIO concurrency, operations, DUCC and authenticated browser tests passed. [PR #42](https://github.com/KKnFinite/NeoApps/pull/42) |
| P3 | NeoRaiden index and `app/static/js/neoraiden.js` | The normal and TV interfaces rendered a refresh countdown and the client woke every second to update it. | Remove the visible countdown and schedule wake-ups at actual deadlines. Manual refresh, the last successful time, stale indication, retry handling and the physical-hours gate remain. Verified at desktop, phone and TV sizes. [PR #38](https://github.com/KKnFinite/NeoApps/pull/38) |
| P3 | `app/neonodes/neoraiden/services.py`, `gateway_operations` | Selecting a Sort deferred its archive timestamp and immediately read it in the physical-hours gate. A conditional 304 paid the extra round trip too. | Include the timestamp in the existing selected-operation query. Tests verify the archive guard and gated sources. [PR #40](https://github.com/KKnFinite/NeoApps/pull/40) |
| P3 | CSS cascade manifest and two shared stylesheets | Four node-specific fragments were delivered globally. Five existing fragment fingerprints were out of date. | Assign the original files to their owning nodes, keep their cascade positions, consolidate two adjacent pairs of identical declarations and refresh the manifest fingerprints. [PR #39](https://github.com/KKnFinite/NeoApps/pull/39) |

## PostgreSQL and application measurements

A signed-in HTTP client exercised current application routes against an ephemeral PostgreSQL server on loopback. Each request had a fresh application context. A transparent local TCP relay counted both directions. SQLAlchemy events counted executed statements and ORM loads. Elapsed time is the median of three warm requests. Python peak allocation was measured in a separate tracemalloc run. The three sizes varied the mission population. Employee, truck, door and crew populations were not independently scaled.

| Sampled route | SQL at 2 / 52 / 156 missions | ORM objects at 52 | DB bytes sent / received at 52 | Median ms | Python peak bytes | HTTP body bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Gateway launcher | 23 / 23 / 23 | 26 | 22,205 / 20,337 | 30.4 | 346,055 | 40,685 |
| MotherBrain Parking Plan changed state | 18 / 18 / 18 | 65 | 26,896 / 41,298 | 17.9 | 822,359 | 78,859 |
| Raiden selected Sort changed state | 25 / 25 / 25 | 59 | 27,007 / 23,126 | 22.6 | 448,267 | 34,438 |
| Ermac View Outbound | 30 / 30 / 30 | 80 | 38,951 / 53,640 | 32.7 | 933,419 | 114,671 |
| Scorpion Fuel Dispatch | 76 / 76 / 76 | 96 | 110,667 / 104,655 | 104.2 | 3,458,620 | 433,562 |
| SubZero DUCC | 57 / 57 / 57 | 83 | 75,961 / 76,215 | 51.1 | 1,875,868 | 300,259 |
| Sektor Driver Routing | 29 / 29 / 29 | 34 | 40,267 / 27,135 | 31.8 | 659,122 | 45,387 |
| Rain Inbound | 37 / 41 / 41 | 142 | 53,165 / 93,803 | 46.0 | 2,259,962 | 326,837 |
| Staffing Attendance | 27 / 27 / 27 | 11 | 27,296 / 18,360 | 17.4 | 517,571 | 17,074 |

The flat counts on eight of nine routes give no evidence of a mission-count N+1 in this sample. Rain added four statements between two and 52 missions and none between 52 and 156. Initial HTML still grew substantially. A large service module on its own did not predict SQL count, response size or elapsed time.

### Measured changes

| Workload | Before | After |
| --- | ---: | ---: |
| Raiden, 52 missions, changed / unchanged SELECTs | 25 / 18 | 24 / 17 |
| Raiden unchanged, database wire bytes | 31,654 | 31,546 |
| Raiden unchanged, median ms / Python peak bytes | 19.5 / 340,898 | 23.1 / 333,067 |
| Planning initial departure page, 52 missions, SQL | 44 original, 43 after the save fix | 33 |
| Same page, database wire bytes | 109,750 original, 105,296 after the save fix | 97,855 |
| Planning changed state, 52 missions, raw / gzip bytes | 241,850 / 8,082 | 113,379 / 6,294 |
| Planning changed state, 156 missions, raw / gzip bytes | 1,461,147 / 30,888 | 329,388 / 14,692 |
| Same large refresh, median ms / Python peak bytes | 34.7 / 5,842,041 | 26.0 / 1,973,520 |
| Same large refresh, database bytes / SQL | 115,187 / 17 | 115,187 / 17 |

The Planning payload comparison uses PR #41 as the control, so the savings belong to PR #43. The initial 156-mission page still delivers about 1.51 MB uncompressed, including the native selects. Raiden saved one query and 108 wire bytes per selected-Sort read. Local timing varied enough that a Raiden latency improvement is unproven.

## Stylesheet and rendering results

The baseline has 96 CSS files and 1,599,415 LF-normalized source bytes. The manifest has 28 numbered fragments plus the base. Thirteen entries, including the base, were marked shared and their actual contents totaled 564,848 bytes. Parsing qualified rules, including nested media rules, found 10,682 rules, 34,696 declarations and 208 important declarations. There were 26,784 repeated property/value/importance occurrences across selectors. Only two occurrences repeated a complete selector, declaration list and enclosing condition under this parser. They involved the mobile Gateway Matrix heading and Ermac belt block. Identical values across components, intentional overrides and rules for inactive states have different operational purposes.

The table uses a signed-in, cold Chromium page at 1366×768, with actual Resource Timing reads of the locally served stylesheets. The local server encoded stylesheets with gzip level 6. The coverage percentage is the union of used rule ranges at initial load, divided by the tracked stylesheet length. Dynamic states, alternate roles, other screen sizes and TV can activate more rules.

| Screen | CSS files before / after | Decoded bytes before | Gzip body bytes before / after | Rules used by source length |
| --- | ---: | ---: | ---: | ---: |
| Gateway | 19 / 15 | 602,112 | 91,998 / 90,101 | 4.2% |
| MotherBrain Parking | 21 / 17 | 624,252 | 97,360 / 95,463 | 6.7% |
| Raiden | 20 / 16 | 616,070 | 95,899 / 94,002 | 4.7% |
| Ermac | 24 / 21 | 670,866 | 104,688 / 103,650 | 4.6% |
| Scorpion | 22 / 18 | 745,262 | 117,329 / 115,432 | 7.5% |
| SubZero | 24 / 21 | 722,607 | 115,959 / 114,911 | 8.6% |
| Sektor | 23 / 19 | 670,715 | 102,630 / 100,733 | 5.2% |
| Rain | 25 / 21 | 675,602 | 106,172 / 104,275 | 5.6% |
| Staffing | 22 / 20 | 853,154 | 131,920 / 130,212 | 1.4% |

Under the same 760 px media condition, `10-shared.css` declares `.gateway-matrix-heading { display: none; }` twice. In `11-neoermac.css` the later 7 px belt-block padding supersedes its earlier identical declaration. These two sub-100-byte examples have little transfer impact. The adjacent shared status rules now use one declaration block, as do the two mobile Planning list layouts. Separate node themes, logos and operational palettes were retained. A new global token layer could not be justified from matching values alone.

With coverage tracing enabled, the initial Chromium Parking Plan render recorded approximately 43 ms of layout and 36 ms of style recalculation. Fuel Dispatch recorded 37 ms and 383 ms. The coverage tracer itself adds overhead, especially on large DOMs. The independent resize sample below used only the performance counters.

The CSS-only comparison included 44 paired images across Chromium and WebKit, 1366×768 and 390×844, including Raiden mobile light/dark and desktop TV light/dark. Forty-three pairs were pixel-identical. Fourteen pixels differed in the Chromium desktop Fuel Dispatch sample. A second control with unchanged Fuel Dispatch styles showed variation at the same coordinates. Inspection showed the same controls, text and geometry. The Raiden-specific suite also exercised TV at 720p, 1080p and 4K. The Staffing screenshot exercised the empty selected-scope state. Record-heavy attendance has separate data tests.

## JavaScript and operational reliability

Eight Chromium width changes on a loaded 52-mission screen took approximately 2.1 ms additional script time on Fuel Dispatch, 2.0 ms on DUCC and 0.9 ms on Raiden in an instrumented local run. DUCC spent approximately 250 ms on style recalculation over those resizes. Listener counts remained at 358, 836 and 60 respectively. These are single-run profiler observations.

Both browser engines made 12 manual unchanged Raiden refreshes with 12 HTTP 304 responses and zero board replacements. In an additional Chromium run, 72 further unchanged reads left 86 listeners throughout the three batches and no board replacements. Post-GC heap measured 2.445, 2.447 and 2.448 MB. This exercise did not establish a persistent leak. It does not cover a full employee shift.

The cross-node runs covered scopes and denials, independent mission identities, stale writes, notes and drafts, fueling deltas, deicing and RADIO, attendance transfers, Google matching through isolated fixtures, and physical Sort boundaries. The manual Planning reconciliation is linked to the operator's action. Automatic refresh continues through the physical-window controller. No synthetic production writes were part of verification.

Fourteen JavaScript cases failed in the starting fixtures. The Rain harnesses omitted the shared physical-window dependency. The Fuel Dispatch harness extracted a function without its account-row selector. Supplying both actual dependencies restored the original assertions. Two added Rain cases verify that open pages make zero requests when the shared physical window is closed. The integrated JavaScript suite passed 387/387.

## Release evidence

| Change | Verified live Git revision | Render deployment |
| --- | --- | --- |
| #38 Raiden countdown | `a8584792ede85037eadb8c5f54cfafb4779b9d37` | `dep-dc21c4u417fc73c4rdd0` |
| #39 CSS delivery | `c2fac747eb79747a586a1685ad470733a3ef7821` | `dep-dc21k360tbcc73ba5hig` |
| #40 Raiden SQL | `433a0fcedaf4013f2c3142a5b9776f82924465aa` | `dep-dc21ldp42hec738hg3ng` |
| #41 Planning integrity | `de21bc345fe455767a63c520b2c9676cfa58e7a4` | `dep-dc21o23bc2fs73eu7vd0` |
| #42 SubZero cold import | `b7a3474a96117665005c4ef6e526fce200931c18` | `dep-dc21pabbc2fs73eu9n50` |
| #43 Planning payload and SQL | `70bca364248c882a0af8abaef897e1c603d0a05b` | `dep-dc21rkdg1s2s73btui70` |

The two initial-page query budgets that failed on the original baseline pass with their original limits after #43. The selected Planning route and browser suite passed 24/24. The separate PostgreSQL browser regression passed with both engines. The Raiden app and browser release run passed 11/11, and the later archive-query suite passed 10/10. The SubZero cold-import change passed 19 PostgreSQL tests and four browser tests. These runs overlap and should not be added as unique test cases. Public production checks used health, redirects and deployed assets. Mutation tests used disposable data.

## Remaining measured work

| Priority | Evidence | Next bounded check |
| --- | --- | --- |
| P2 | A large initial Departure Planning page still carries 1.51 MB raw. The compact changed response now carries 329 KB. | Compare one shared progressively enhanced initial picker with the full native picker on WebKit, including keyboard entry, offline rendering and draft restoration, before changing initial delivery. |
| P2 | At 156 missions the initial Fuel Dispatch and Rain Inbound pages returned 1,167,803 and 878,853 bytes. | Measure the static and dynamic contribution of desktop and mobile form markup separately. Preserve each field, authorization scope, and recovery draft in any delivery change. |
| P3 | Only 1.4–8.6% of tracked CSS source ranges matched at first desktop paint. | Identify the largest whole page-owned blocks in the shared fragments. Compare interaction and role-specific states before removing or moving a rule. |
| P3 | DUCC style recalculation consumed about 250 ms across eight desktop resizes in one local run. | Repeat on representative hardware with a populated truck and crew fixture before changing the grid. |

Production dollar savings and hosted CSS transfer by endpoint are unmeasured. The existing November 25 comparison remains the source for the separate infrastructure question.
