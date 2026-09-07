"""Additive Western Latin coverage; approved base outlines are never edited.

Precomposed letters use exact finalized base contours plus reusable marks.
Non-decomposing letters use explicit ligature/stroke recipes or new skeletons.
"""
import unicodedata

ACCENTS = {
    '\u0300': ['M 65 20 L -45 105'],  # grave
    '\u0301': ['M -65 20 L 45 105'],  # acute
    '\u0302': ['M -90 20 L 0 100 L 90 20'],
    '\u0303': ['M -95 35 Q -55 110 0 60 Q 55 10 95 85'],
    '\u0308': ['M -65 30 L -65 90', 'M 65 30 L 65 90'],
    '\u030A': ['M -42 58 Q -42 100 0 100 Q 42 100 42 58 Q 42 16 0 16 Q -42 16 -42 58 Z'],
    '\u0327': ['M 0 -15 L -25 -65 L 40 -65 Q 75 -140 -35 -145'],
    '\u0304': ['M -95 60 L 95 60'],  # macron
    '\u0306': ['M -95 95 Q -75 15 0 15 Q 75 15 95 95'],
    '\u0307': ['M 0 30 L 0 90'],
    '\u030B': ['M -105 20 L -30 105', 'M 15 20 L 90 105'],
    '\u030C': ['M -90 100 L 0 20 L 90 100'],
    '\u0328': ['M 45 -5 Q -70 -80 -20 -140 Q 15 -170 70 -130'],
}

COMPOSED = {}
for code in range(0xC0, 0x180):
    char = chr(code)
    parts = unicodedata.normalize('NFD', char)
    if len(parts) == 2 and parts[0].isascii() and parts[0].isalpha() and parts[1] in ACCENTS:
        COMPOSED[char] = tuple(parts)

# Use existing base shapes intact, translated into a single joined character.
LIGATURES = {'Æ': ('A', 'E', 415), 'æ': ('a', 'e', 350),
             'Œ': ('O', 'E', 405), 'œ': ('o', 'e', 350)}
BARRED = {'Ø': ('O', 'M 140 90 L 480 630'),
          'ø': ('o', 'M 140 70 L 430 450'),
          'Ð': ('D', 'M 35 360 L 330 360'),
          'Ł': ('L', 'M 40 245 L 305 470'),
          'ł': ('l', 'M 45 280 L 245 455')}
NEW = {
    'ẞ': (670, ['M 105 0 L 105 720 L 370 720 Q 515 720 515 570 Q 515 485 375 390 L 550 180 Q 595 32 365 32 L 285 32']),
    'Þ': (600, ['M 105 0 L 105 720', 'M 105 575 L 350 575 Q 510 575 510 395 Q 510 215 350 215 L 105 215']),
    'þ': (580, ['M 105 -200 L 105 760', 'M 105 365 Q 105 488 265 488 Q 475 488 475 265 Q 475 32 265 32 Q 105 32 105 155']),
    'ð': (580, ['M 150 730 Q 490 620 490 310 L 490 195 Q 490 32 285 32 Q 90 32 90 215 L 90 275 Q 90 450 285 450 Q 490 450 490 275', 'M 115 575 L 410 730']),
    'ß': (610, ['M 105 0 L 105 535 Q 105 735 285 735 Q 455 735 455 580 Q 455 480 320 385 Q 510 315 510 175 Q 510 32 335 32 L 250 32']),
}
