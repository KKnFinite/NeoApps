from pathlib import Path

css_path = Path("app/static/css/base.css")

block = r'''
/* NeoFont locked opt-in branding/title utilities only.
   Do not apply NeoFont globally to body, buttons, tables, forms, or normal UI text. */
@font-face {
  font-family: "NeoFont";
  src:
    url("/static/fonts/neofont/NeoFont.woff2") format("woff2"),
    url("/static/fonts/neofont/NeoFont.ttf") format("truetype");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}

.neo-brand-title,
.neo-page-title {
  font-family: "NeoFont", Arial, sans-serif;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.neo-brand-title__neo {
  color: #DDEBFF;
}

.neo-brand-title__node--gateway {
  color: #FF5A1F;
}

.neo-brand-title__node--staffing {
  color: #21E6C8;
}

.neo-brand-title__node--bid {
  color: #3DA7FF;
}

.neo-brand-title__node--motherbrain {
  color: #FF7A7A;
}

.neo-brand-title__node--sektor {
  color: #E51B23;
}

.neo-brand-title__node--ermac {
  color: #D71F2A;
}

.neo-brand-title__node--scorpion {
  color: #FFC928;
}

.neo-brand-title__node--reptile {
  color: #39FF6A;
}

.neo-brand-title__node--subzero {
  color: #5EDCFF;
}

.neo-brand-title__node--rain {
  color: #A45CFF;
}
'''

text = css_path.read_text(encoding="utf-8")

marker = "NeoFont locked opt-in branding/title utilities only"

if marker not in text:
    css_path.write_text(text.rstrip() + "\n\n" + block.lstrip(), encoding="utf-8")
    print("NeoFont opt-in CSS added to", css_path)
else:
    print("NeoFont opt-in CSS already exists in", css_path)
