from pathlib import Path
import cv2

letters_dir = Path("app/static/fonts/neofont/work/letters")
out_dir = Path("app/static/fonts/neofont/glyphs")
out_dir.mkdir(parents=True, exist_ok=True)

letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

for letter in letters:
    src = letters_dir / f"{letter}.png"
    img = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)

    if img is None:
        print(f"missing: {src}")
        continue

    h, w = img.shape

    # White letters on black background.
    _, thresh = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)

    contours, hierarchy = cv2.findContours(
        thresh,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE
    )

    paths = []

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 20:
            continue

        # Lower epsilon = closer to source. Do not over-simplify.
        epsilon = 1.0
        approx = cv2.approxPolyDP(contour, epsilon, True)

        points = approx.reshape(-1, 2)
        if len(points) < 3:
            continue

        d = [f"M {points[0][0]} {points[0][1]}"]
        for x, y in points[1:]:
            d.append(f"L {x} {y}")
        d.append("Z")
        paths.append(" ".join(d))

    svg_path_data = " ".join(paths)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">
  <path d="{svg_path_data}" fill="#000000" fill-rule="evenodd"/>
</svg>
'''

    out = out_dir / f"{letter}.svg"
    out.write_text(svg, encoding="utf-8")
    print(f"created: {out}")

print("done")
