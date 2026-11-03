# NeoSubZero approved status palette

Starting main: `b1b2cb0a69be0865f5e608cb73c11d3d6f47cf9d`.

Presentation-only release. Application changes are three stylesheets and stylesheet cache-version references in the shared base and Operations templates. No services, JavaScript behavior, models, schemas, permissions, queue rules, sorting or operational records change.

| Status | Approved fill | Text |
| --- | --- | --- |
| PLANNED / INITIAL CONTACT | `#D4A536` | `#101B26` |
| CONFIGURED | `#278756` | Black |
| DEICING | `#D9EDF0` | `#101B26` |
| FIN | `#285DA8` | White |
| RADIO PENDING | `#F02D9F` | `#101B26` |
| NEGATIVE DEICE | `#C34854` | White |
| GONE / RADIO COMPLETE / NO DEICE | `#687480` | White |

The shared variables and state aliases live in `neosubzero_workspace.css`. Operations and DUCC use those exact fills, corresponding borders and foregrounds. Smaller DUCC labels and monochrome aircraft symbols inherit the state foreground. Hover/selection retains the status color; keyboard focus has a visible outline. Review/conflict and explicit NOT SPRAYED treatments remain distinct. The pink readback section uses the same shared radio color.

Black is deliberate on the approved emerald: white would be just below the 4.5:1 normal-text target. Removing translucent pass-label text prevents it from reducing contrast. Both browsers measured a minimum status-text contrast of **4.625:1** across the approved states.

## Validation

- Python: **27 passed, 11 passing subtests** for radio, Operations, mobile ramp and navigation.
- JavaScript: **5 passed**; Operations/UCC syntax checks passed.
- Browser: **4 passing unique cases**, comprising Chromium/WebKit palette checks and the existing full radio lifecycle in both engines. The palette cases were rerun successfully after correcting the test harness to use keyboard input for keyboard-focus assertions.
- Palette checks cover **36 viewport/layout combinations**: list and visual Operations plus DUCC at 320, 375, 390, 430, 1440 and 1920 pixels, in each browser. Assertions verify exact computed fills, every label's contrast, review separation, hover/selection/focus, no page overflow, and unchanged canonical event snapshots.
- Radio regression retains Position 10 through CLEAR, blocks the next START, preserves drafts, releases only on explicit radio confirmation, and checks physical-tail review. An automatically GONE no-deice aircraft remains absent from DUCC, rather than creating a phantom to display gray.
- Python compilation and `git diff --check` passed. Existing datetime/ORM warnings remain visible. No full-system or load suite is claimed; PostgreSQL tests were unnecessary for CSS/cache-reference changes.

Synthetic browser records and local screenshots/results are under `instance/subzero-palette/`; screenshots are in `browser/`. Production verification is read-only: exact Render AutoDeploy identity, successful bootstrap, health/login, authenticated Operations/DUCC, CSS asset hashes and rollout errors. The final SHA/deployment ID are provided in the release completion response.

Production aircraft, treatment history, Google Sheets, FROST evidence, R2 objects, hosting settings and unrelated working-tree files are preserved.
