from pathlib import Path
from PIL import Image, ImageOps

src = Path("app/static/fonts/neofont/source/neo-alphabet-specimen.png")
out_dir = Path("app/static/fonts/neofont/work")
out_dir.mkdir(parents=True, exist_ok=True)

img = Image.open(src).convert("RGB")

gray = ImageOps.grayscale(img)
gray = ImageOps.autocontrast(gray)
gray.save(out_dir / "neo-alphabet-gray.png")

threshold = 170
bw = gray.point(lambda p: 255 if p >= threshold else 0, mode="L")
bw.save(out_dir / "neo-alphabet-bw-preview.png")

print("created:", out_dir / "neo-alphabet-gray.png")
print("created:", out_dir / "neo-alphabet-bw-preview.png")
print("threshold:", threshold)
