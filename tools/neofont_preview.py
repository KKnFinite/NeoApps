from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

font_path = Path("app/static/fonts/neofont/NeoFont.ttf")
out_path = Path("app/static/fonts/neofont/work/neofont-preview.png")

img = Image.new("RGB", (2200, 1200), "#07111f")
draw = ImageDraw.Draw(img)

font_big = ImageFont.truetype(str(font_path), 150)
font_mid = ImageFont.truetype(str(font_path), 110)

lines = [
    ("ABCDEFGHIJKLMNOPQRSTUVWXYZ", font_big, 80, 120),
    ("NEOAPPS NEOGATEWAY", font_mid, 80, 390),
    ("NEOSTAFFING NEOMOTHERBRAIN", font_mid, 80, 570),
    ("MB SK ER SC RP SZ RN", font_big, 80, 780),
]

for text, font, x, y in lines:
    draw.text((x, y), text, font=font, fill="#ffffff")

img.save(out_path)
print("created:", out_path)
