from pathlib import Path
from PIL import Image

src = Path("app/static/fonts/neofont/work/neo-alphabet-bw-preview.png")
out_dir = Path("app/static/fonts/neofont/work/letters")
out_dir.mkdir(parents=True, exist_ok=True)

img = Image.open(src).convert("L")
w, h = img.size
px = img.load()

# Find rows that contain white pixels.
row_counts = []
for y in range(h):
    count = 0
    for x in range(w):
        if px[x, y] > 200:
            count += 1
    row_counts.append(count)

# Group active rows.
row_groups = []
in_group = False
start = 0
for y, count in enumerate(row_counts):
    active = count > 20
    if active and not in_group:
        start = y
        in_group = True
    elif not active and in_group:
        if y - start > 20:
            row_groups.append((start, y - 1))
        in_group = False
if in_group:
    row_groups.append((start, h - 1))

letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
letter_index = 0

for row_start, row_end in row_groups:
    # Find columns with white pixels inside this row band.
    col_counts = []
    for x in range(w):
        count = 0
        for y in range(row_start, row_end + 1):
            if px[x, y] > 200:
                count += 1
        col_counts.append(count)

    col_groups = []
    in_col = False
    col_start = 0
    for x, count in enumerate(col_counts):
        active = count > 10
        if active and not in_col:
            col_start = x
            in_col = True
        elif not active and in_col:
            if x - col_start > 20:
                col_groups.append((col_start, x - 1))
            in_col = False
    if in_col:
        col_groups.append((col_start, w - 1))

    for col_start, col_end in col_groups:
        if letter_index >= len(letters):
            break

        pad = 40
        left = max(0, col_start - pad)
        top = max(0, row_start - pad)
        right = min(w, col_end + pad)
        bottom = min(h, row_end + pad)

        letter = letters[letter_index]
        crop = img.crop((left, top, right, bottom))
        crop.save(out_dir / f"{letter}.png")
        print(letter, (left, top, right, bottom))

        letter_index += 1

print("letters created:", letter_index)
print("output:", out_dir)
