# Registration Employee / UPS ID validation

Starting main: `9b22eb0cbe31254ec1758283e1eab8309a38e5a7` (fetched and verified).

The owner's final requirement supersedes the initial CRN format: **exactly seven or ten ASCII digits**, using `^(?:[0-9]{7}|[0-9]{10})$`. The field is mandatory for Full NeoApps and Fuel Truck Driver signup. Surrounding whitespace is trimmed; leading zeros remain intact. CRN, signs, decimal notation, embedded whitespace, letters, non-ASCII digits and other lengths reject with a clear format error.

The shared `/create-account` backend validates before account creation, password hashing, grants, tokens and email. Existing registration rate-limit admission remains intact. Browser validation uses the same pattern, a numeric mobile keyboard and accessible validation messages. No existing User, Staffing record, account-edit validation, administrative UPS-ID correction, permission or schema is changed. Existing duplicate-ID locking and Staffing matching remain in place.

Changed application files: `app/auth/routes.py`, `app/templates/auth/create_account.html`, `app/static/js/accounts.js`. Registration fixtures were updated to valid synthetic IDs; the old optional-ID registration test now asserts rejection. No historical-account fixtures were converted globally.

Focused final verification:

- **30 Python cases and 55 passing subtests**: ID boundaries, regular/driver requests, whitespace/leading zeros, normalized duplicates, preserved Staffing identity, missing fields, CSRF, password rules and abuse protection. The 72 deselected cases were outside the focused registration selection, not reported as passes.
- **Three additional compatibility cases passed**: Fuel Truck Driver signup, multiple-application pending requests/history and legacy-account login.
- **Nine PostgreSQL concurrency/rollback cases passed** in a new isolated schema on the existing loopback synthetic database. No production writes.
- **One browser case with two passing subtests**: desktop Chromium and mobile WebKit, both account types' ID validity, normalization, actual registration, pending access, enforced CSP/CSRF, rate-limit handling and draft retention. Current renewal markup can contain multiple matching CSRF inputs; the test verifies all are nonempty and identical within the registration form.
- JavaScript syntax and `git diff --check` passed. Existing deprecation warnings remain visible in the evidence logs. No broad suite was run.

Evidence: ignored `instance/registration-id-final.log`, `registration-id-compatibility.log`, `registration-id-postgres.log`, `registration-id-browser-result.log`.

## Release gate

At November 18, 23:01 CST, read-only production settings confirm RFD's Wednesday night Sort is enabled **21:00–05:00 America/Chicago**. The current Sort record is present and unarchived. The owner requires deployment outside an active Sort; **the AutoDeploy-triggering push is held until after the configured window and a fresh operational-safety check**. No active-Sort deployment, production signup, account mutation or GitHub Issue was performed.

The existing Render deployment remains LIVE at `9b22eb0cbe31254ec1758283e1eab8309a38e5a7`, deployment `dep-dbv5si7f3r2c73fghff0`. This is the previous release, not a claim that the new validation is LIVE. Final release requires a fresh fetch, preserving any concurrent main changes, rechecking the safe window, pushing the verified commit, and confirming the resulting exact Render SHA, startup, health and registration asset/form attributes without production registration POSTs.
