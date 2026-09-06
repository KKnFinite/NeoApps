NeoApps locked icon/logo pack

Approved source: NeoApps_Approved_Icon_Master_v2.png, 1254x1254 RGB PNG.
Chrome NA mark and energy slash in the red/blue cosmic portal environment.
SHA-256: c226ebbb10167a3cd5c8b9d553147999e9befd8a647a031470245036a9bd18dd
source/neoapps-icon-original.png is a byte-for-byte copy of the approved source.
source/neoapps-icon-master-1024.png is its Lanczos-downsampled canonical master.
All other sizes derive from that master without recoloring, cropping or sharpening.
Regenerate: python scripts/generate_neoapps_icons.py <approved-source.png>
Use pwa/ for install/home-screen manifest icons.
Use inapp/ for Portal/UI branding where a full square icon is desired.
Use favicon/ for browser tab/favicon assets.

Maskable PNGs center the full unchanged artwork at 56% on an opaque black canvas;
even its corners fit inside the guaranteed 80%-diameter mask-safe circle.
Opaque square Apple icons retain the centered mark under rounded-square masking.
No artwork is pre-rounded. ICO retains its existing 16/32/48/64 frames.

The authoritative manifests are dynamic /manifest.webmanifest and
/manifest/neoapps.webmanifest, referenced through base.html. They already use
/static/images/icons/neoapps/pwa/. The legacy /static/manifest.webmanifest is not
the application manifest and is intentionally not maintained by this generator.
Apple/favicon routes and shared headers already reference this canonical family.
For wide transparent branding use the separate images/logos/neoapps/ pack.
Do not stretch these square icons or replace node-specific artwork.
