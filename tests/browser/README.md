# Shared mobile drawer browser checks

These tests run the real Flask routes behind a temporary local HTTP server.
They create a temporary SQLite database and synthetic administrator/watcher
accounts, use the normal email/password login with CSRF protection enabled,
disable Google/Flight API configuration, and block external browser requests
and server-side requests made through requests. Nothing connects to production
Neon. The server and database are closed at the end of the run.

Install development-only tooling:

    python -m pip install -r tests/requirements-browser.txt
    python -m playwright install chromium webkit

Run from the repository root:

    python -m unittest tests.browser.test_mobile_drawer -v
    node --test tests/js/mobile_drawer.test.js

Chromium and WebKit each exercise Portal, Gateway, and MotherBrain at
320x700 and 390x844, plus a separate mouse/keyboard
1920x1080 desktop context. Coverage includes dismissal methods, focus,
scrolling, mutually exclusive Nodes/Menu modes, navigation, reduced motion, safe-area simulation, Board View,
restricted permissions, and unchanged NeoStaffing navigation. The last real
Gateway card is checked above the dock and its fade after scrolling, and header
product names are measured after the actual NeoFont loads.

Screenshots default to the ignored directory
`instance/browser-evidence/mobile-drawer/`. Set `NEO_BROWSER_EVIDENCE` to use
another directory. Fonts and images are awaited before measurements/screenshots.

Optionally set `NEO_COMPARE_BASELINE` to a local git revision before running.
This additionally compares Scorpion desktop header/sidebar geometry with that
revision's CSS. It detects changes without altering existing desktop behavior.
It does not assert that every pre-existing desktop layout is free of defects.

Browser viewport and safe-area emulation do not replace physical iPhone,
Safari, or installed-PWA testing. Swipe direction is tested with touch pointer
events; vertical scrolling is additionally exercised with native wheel input.
