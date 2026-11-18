# Portal navigation and header correction

## Result

Implemented and verified locally from origin/main `bb4ecd9a95b294c861d926d89437fa903a0b9cd2`. Release is held during the active RFD night Sort; no push, production deployment, worker restart, permission change or scoped-role activation is performed by this package.

The removed `.aa-apps-extra` was a second flex child beside the launcher grid. It consumed the cards' horizontal space and placed account links over the artwork. The generic desktop header also mixed independently styled controls and rendered an App Switcher link back to the Portal itself.

## Changes

* Removed floating utilities from `app/templates/auth/portal.html` and their unused rules from `app/static/css/accounts.css`.
* Added the Portal-only `app/templates/auth/_portal_header.html` and `app/static/css/portal_navigation.css`, wired through `app/templates/base.html`. Controls share 40px height, system typography, padding, borders, hover and keyboard-focus styling. Large desktop uses one row; 901–1199px uses two intentional aligned groups. Long identities truncate visually while retaining their full text/title.
* `app/templates/auth/_account_links.html` omits App Switcher only on the Portal. My Account remains available elsewhere.
* The existing bottom MENU already contains My Account and permission-filtered Accounts & Access. These remain there. App Access stays in My Account.
* Optional CryoCheck remains preference-controlled, with the existing external destination, `target=_blank` and `noopener noreferrer`. It now appears in the desktop header/mobile MENU, outside the artwork. Added its MENU link in `app/templates/_mobile_drawer.html`.

Launcher cards, images, bottom navigation, character-selection/alert behavior, application approval checks, scoped-role services, grants and operational code remain unchanged.

## Validation

* **26 Python cases passed**: four new rendered-navigation cases, existing Accounts workspace and shell-metadata coverage. Final log: `instance/portal-navigation-python-final.log`.
* **Chromium and WebKit passed**: 14 administrator engine/viewport scenarios plus four ordinary-account checks. Sizes: 375×667, 390×844, 430×932, 960×800, 1024×768, 1280×900 and 1440×900. Verified card/dock separation, no horizontal overflow, identical control dimensions/typography, working character and Alerts menus, My Account → App Access, authorization-filtered administration links and opt-in external shortcut. Final log: `instance/portal-navigation-browser-final.log`.
* Actual before/after screenshots were inspected. Evidence: `instance/browser-evidence/portal-navigation/{before,after}/portal-<engine>-<width>.png`; mobile MENU screenshots are in `after/menu-<engine>-<width>.png`.
* Synthetic fixture checks confirm zero authorization-state, scoped-role or security-event records created by navigation. Existing temporary databases, jobs and unrelated files are retained. External providers are blocked in the browser fixture.
* The first Python harness run lacked BeautifulSoup. The new tests now use the standard-library HTML parser; no dependency was added, no warning was suppressed and no test was skipped.

No PostgreSQL persistence or JavaScript changes require additional transaction/JavaScript suites. Python/Jinja syntax and diff whitespace are checked before commit. This is focused UI verification, not a full-system audit.

## Deployment hold and release procedure

Read-only production evidence at **2026-11-18 01:10 Central** confirmed:

* RFD night Sort configured **21:00–05:00**, Monday–Thursday nights; the separate operations window ends 04:00.
* Current unarchived night operation: ID 93, Sort date November 17.
* Phase 5 authorization cutover still has **zero marker rows**, preserving its pending state.
* Render remains LIVE at `bb4ecd9a95b294c861d926d89437fa903a0b9cd2`, deployment **`dep-dbujoarbc2fs73af6cdg`**.

Main AutoDeploy replaces production workers. Publishing during this active Sort would violate the owner's explicit release condition. The verified fix is retained on a local `codex/portal-navigation-header` branch, with main/Render unchanged. No deployment timer, new job or infrastructure change is introduced.

For release after 05:00 Central on November 18 (11:00 UTC), first recheck the database-configured windows and actual operational activity. Fetch latest main and safely integrate this commit, preserving concurrent work. Rerun affected checks if integration changes code, push through existing main AutoDeploy, then verify the exact SHA LIVE, startup, health/login, the new CSS bytes and authorized Portal views. Confirm the pending scoped-role state is unchanged; do not activate it as part of this UI release.
