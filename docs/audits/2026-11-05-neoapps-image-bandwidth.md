# NeoApps image bandwidth and browser caching — 2026-11-05

Starting main/LIVE SHA: `d8c4e2d825f264c6e850e223ae65ff4a8cc0b456`. Scope: image derivatives, image URL binding and public static caching only. No layout, branding, CSS/JavaScript version policy, permissions, database, operational workflow or background-job changes.

## Corrections

The Change Characters menu selected the 2,242,897-byte, 1254×1254 Scorpion source for a 28×28 display. Its unversioned URL also missed existing immutable caching. `app/__init__.py` now selects a dedicated **64×64 PNG, 10,784 bytes (99.52% smaller)**. The same binding supplies mobile Nodes navigation. The canonical templates remain shared; no duplicate menu is introduced.

`scripts/build_image_derivatives.py` creates 19 derivatives and a source/derivative checksum manifest under `app/static/images/optimized/`. Originals remain byte-for-byte intact. Large shell logos use 288×288 lossless WebP (144px sidebar at 2x density); existing small logos retain all 256×256 pixels with lossless WebP. Resizing preserves aspect ratio and alpha. The menu PNG preserves the exact downsampled RGBA pixels. Large backgrounds and SPEAR artwork retain their original resolution, encoded as quality-94 WebP with alpha preserved. No image generation, recoloring, crop or design change occurred.

Rendered dimensions were measured using the existing isolated authenticated application fixture at 390×844, 1366×768 and 1920×1080 in Chromium and WebKit. Relevant sizes: menu 28px, sidebar 144px, Gateway logos 62–80px, mobile background 390×698, portal background up to 1920×994, SPEAR artwork up to 1320×743. Background source resolution is retained for large screens and mobile pixel density. Existing HTML dimensions, responsive selection, object-fit and CSS are unchanged.

## Exact derivative sizes

Paths below are beneath `images/`; outputs are beneath `images/optimized/`. All originals are retained.

| Original | Original bytes | Optimized bytes | Reduction |
|---|---:|---:|---:|
| `logos/newlogo_scorpion.png` → menu PNG | 2,242,897 | 10,784 | 99.52% |
| `logos/newlogo_scorpion.png` → sidebar/Gateway WebP | 2,242,897 | 116,312 | 94.81% |
| `logos/newlogo_motherbrain.png` | 2,226,031 | 111,096 | 95.01% |
| `logos/newlogo_sektor.png` | 2,144,075 | 99,936 | 95.34% |
| `logos/newlogo_ermac.png` | 2,240,980 | 128,014 | 94.29% |
| `logos/newlogo_rain.png` | 2,597,290 | 122,316 | 95.29% |
| `logos/newlogo_subzero.png` | 3,012,590 | 131,114 | 95.65% |
| `logos/newlogo_motherbrain_small.png` | 121,999 | 89,700 | 26.47% |
| `logos/newlogo_sektor_small.png` | 123,854 | 81,714 | 34.02% |
| `logos/newlogo_ermac_small.png` | 130,032 | 104,368 | 19.74% |
| `logos/newlogo_rain_small.png` | 144,173 | 99,474 | 31.00% |
| `logos/newlogo_subzero_small.png` | 151,762 | 106,340 | 29.93% |
| `logos/newlogo_reptile_small.png` | 136,081 | 99,908 | 26.58% |
| `hero/neoapps_login_mobile.png` | 2,504,774 | 463,590 | 81.49% |
| `hero/neoapps_login_desktop.png` | 2,500,997 | 469,856 | 81.21% |
| `hero/neoapps_portal_desktop.png` | 2,599,174 | 499,284 | 80.79% |
| `hero/hero_gateway.png` | 2,216,206 | 392,004 | 82.31% |
| `hero/hero_gateway_small.png` | 596,318 | 116,148 | 80.52% |
| `neoscorpion/spear-promo.png` | 1,975,245 | 322,020 | 83.70% |

## Content-based caching

`app/services/image_assets.py` registers image-only URL defaults. Existing `url_for('static', filename=...)` references select approved derivatives where mapped and receive `v=<SHA-256 of the served image>`. Other public images generated through `url_for` also receive content versions. This fixes the shared menu omission without repeating edits in every template. Raw original URLs remain available.

Matching image versions receive the existing **`Cache-Control: public, max-age=31536000, immutable`** policy plus a content-based ETag. Conditional GET/HEAD returns 304 with no body. Wrong/missing content versions do not receive the new image immutable policy. Errors, private pages, manifests, service-worker routes and non-image resources are not covered by this hook. Public-asset authorization bypass remains the existing exact GET/HEAD allowlist.

`STATIC_ASSET_VERSION` currently defaults to `20260928-neoscorpion-seamless-refresh`; production login also exposed that version before release. The repository does **not** automatically derive it from each commit. It can be explicitly changed through configuration/releases. Image URLs now remain stable under either deployment-mtime changes or unrelated STATIC_ASSET_VERSION changes. Changed served artwork gets a new content hash/URL. Rebuild derivatives when their source changes; tests verify both source and derivative checksums. JavaScript/CSS retain their existing release versions.

Hashes are streamed and memoized in a bounded 512-entry process-local cache keyed by file path, size and nanosecond mtime. It stores digests, not image bodies or user data. No database reads, background services or external cache are introduced. Paths must resolve inside the public image directory. The existing PWA service worker is unchanged; normal navigation still goes to the network and its image fetches honor the browser HTTP cache.

## Measured browser network behavior

Actual Resource Timing measurements from isolated Chromium **and** WebKit, with fresh contexts and no request-routing interception (which would disable HTTP caching):

| Image | Original cold body bytes | Optimized cold body bytes | Optimized cold `transferSize` | Repeat navigation / normal reload |
|---|---:|---:|---:|---|
| Scorpion menu | 2,242,897 | 10,784 | 11,084 | 0 transferred bytes, 0 server image requests |
| Scorpion sidebar | 2,242,897 | 116,312 | 116,612 | 0 transferred bytes, 0 server image requests |
| Mobile login artwork | 2,504,774 | 463,590 | 463,890 | 0 transferred bytes, 0 server image requests |

`transferSize` is the browser's HTTP transfer metric, including its header allowance; it is not a packet capture or Render invoice measurement. Original cold transfers measured 2,243,197 bytes for Scorpion and 2,505,074 bytes for mobile artwork. Hard reload/cache clearing, cache eviction and a new browser profile can still download an asset. First navigation after release must receive the new image URLs.

Current content version survives a simulated unrelated release and mtime-only change. Changing actual image bytes produces a new URL. Server tests verify exact ETag, immutable headers, GET/HEAD, empty-body 304, incorrect versions, missing files and path-escape rejection. Existing anonymous/regular/vendor/revoked-session and unavailable-user-database tests remain passing; no authentication/vendor SQL is needed for public assets.

## Projection from the previous actual Sort

The prior Nov 4–5 census measured **275 successful GETs of the full Scorpion PNG**, reconstructed as 616,796,675 source bytes. Those requests cover multiple shared uses; they cannot all be asserted to have been menu fetches. Conservatively substituting the **larger** 116,312-byte Scorpion derivative for every one gives **31,985,800 bytes**, a projected **584,810,875-byte reduction** before any browser-cache benefit. Menu requests are smaller still.

For all mapped images observed in that census, retaining each actual successful GET count and conservatively using the largest matching derivative yields **1,472,594,306 → 276,487,360 bytes**: **1,196,106,946 fewer raw body bytes (81.22%)**. This is an encoding-only projection, not realized nightly bandwidth or cost savings. Browser caching can further reduce successful downloads, but no nightly cache-hit percentage is assumed. Do not add these savings twice or apply them to service-initiated outbound traffic.

## Focused validation and evidence

* **22 focused Python tests passed**, including 227 subtests: image integrity/cache contracts, page asset manifest, public request boundaries, selected existing immutable/PWA checks and the two affected portal image URL assertions. Existing datetime/ORM deprecation warnings remain visible.
* **3 browser tests passed**, each exercising Chromium and WebKit: cold/repeat/reload cache delivery; real mobile Nodes and desktop Change Characters plus PWA navigation; anonymous mobile/desktop login artwork and forms. The final added original-cold measurement also passed independently.
* **48 before/after authenticated page/viewport comparisons** across eight entry routes showed **zero image width/height/object-fit/visibility changes**. Authenticated `/login` redirects to Portal; anonymous Login was checked separately. These are presentation measurements, not operational workflow tests. Existing page overflow, if any, was not redesigned.
* Source/derivative hashes, lossless decoded RGBA equality, exact alpha/proportions, Python compilation, unchanged service-worker JavaScript syntax and `git diff --check` passed. No database behavior changed, so PostgreSQL and broad operational suites were not run.

Local ignored evidence: `instance/image-bandwidth-20261105/{before,after}/measurements.json`, before/after screenshots, `cache/network.json`, `cache/rendering.json`, menu and anonymous-login screenshots, `analysis.json`; test logs under `instance/image-*.log`. The reproducible derivative manifest is committed. Browser screenshots were visually inspected for the Gateway, menu and mobile login artwork.

## Remaining limits and release verification

Large approved backgrounds still reach 322–499 KB at their retained full resolution; further quality reduction is deliberately avoided. Existing efficient WebP artwork and unobserved source art were not recompressed speculatively. Raw original paths remain large for compatibility and can still be requested directly by old documents. PWA manifest icon routes keep their existing semantics. Content rewriting applies to generated image URLs, not arbitrary literal strings in external clients.

The previous **1,620.68 MB service-initiated** category still needs destination attribution; this release makes no claim to reduce Neon CU, external API or archive costs. A comparable post-release busy Sort is required to verify realized HTTP bandwidth savings.

Release is through main and existing Render AutoDeploy. The final completion receipt records the exact enclosing commit SHA and deployment ID after LIVE verification; machine-readable local verification is saved as `instance/image-bandwidth-20261105/release.json`. Required checks: bootstrap/worker startup, health/login, all 19 published derivative hashes and immutable/ETag headers, affected page references, existing security headers and rollout errors. No production test mutations are used. Unrelated artwork/prototype files, prior report, operational records and existing jobs are preserved.
