NeoSektor locked icon pack
===========================

Source-approved NeoSektor icon generated from the user-provided SK image.

Design lock
-----------
- Full-bleed dark rounded-square icon.
- Red Sektor/cyber-ninja mask line art.
- Bright red Neo-font SK mark centered.
- No white/transparent corner artifacts.
- Do not redesign, regenerate, or approximate the lettering.

Suggested repo placement
------------------------
Copy this folder's contents to the NeoApps repo icon folder for NeoSektor, keeping this structure:

app/static/images/icons/neosektor/
  source/
  pwa/
  inapp/
  favicon/
  snippets/
  manifest-icon-snippet.json
  README.txt

If your current repo path is using image/icons/neosektor/ as the checked-in icon source folder, keep the same subfolders there and wire the app/static served paths accordingly.

Files included
--------------
source/neosektor-source-original.jpeg  Original uploaded image.
source/neosektor-source.png            Normalized square PNG source.
pwa/android-chrome-192x192.png         PWA/install icon.
pwa/android-chrome-512x512.png         PWA/install icon.
pwa/maskable-icon-192x192.png          Maskable manifest icon.
pwa/maskable-icon-512x512.png          Maskable manifest icon.
pwa/apple-touch-icon.png               Apple touch icon, 180x180.
inapp/neosektor-icon-*.png             In-app branding sizes.
favicon/favicon.ico                    Multi-size ICO.
favicon/favicon-*.png                  PNG favicons.
manifest-icon-snippet.json             Manifest icons array.
snippets/html-head-links.txt           Optional Flask/Jinja head links.

Implementation rule
-------------------
Use this pack for NeoSektor PWA/install icons, apple-touch-icon, manifest entries, favicon, and in-app NeoSektor branding spots where appropriate. Do not touch unrelated image/logo/icon assets.
