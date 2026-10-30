"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

A small, deterministic PDF writer for the documents OpenApps makes.

Bank statements and shop receipts are rendered when the apps save them (the
seeded documents in ``files_app/seed`` were drawn with it too). They are all
text, rules and boxes. Writing them ourselves needs no dependency and, more to
the point, gives **the same bytes for the same spec**: a saved statement keeps
a stable checksum, ``/files_all`` stays byte-stable, and a test can compare a
statement against the ledger it came from. Libraries that stamp creation dates, random
IDs or font subsets can't promise that. pypdfium2 was considered and can't
place text without raw C calls.

Choices that follow from that, and from who reads these files (pdf.js, for
screenshot, accessibility-tree and HTML agents alike):

* **Standard 14 fonts, never embedded:** Helvetica, Helvetica-Bold and Courier.
  Every viewer has their metrics, so a file stays a few KB. The widths below
  are the Adobe AFM widths, which is what makes wrapping and right-aligned
  columns land where the viewer draws them.
* **WinAnsi (cp1252) encoding.** It covers English and German text, currency
  symbols, dashes, curly quotes and bullets. Any character outside it -- CJK,
  emoji, check marks -- is written as ``?``, one per character, so the result
  is predictable rather than garbled. Tabs and newlines inside a text run
  become spaces, and other control characters become ``?``. Content that must
  render in another script needs embedded fonts, which this writer
  deliberately doesn't do.
* **Uncompressed content streams.** The documents are tiny, and the text stays
  greppable in the raw bytes, which keeps the tests to a few regexes.
* **Fixed object order, no timestamps in /Info, a classic xref table.**

Coordinates are in points with the origin at the **top-left** of the page and
``y`` growing downward, the way a layout function thinks. ``y`` for
:meth:`Document.text` is the baseline. The writer flips to PDF's bottom-left
origin on output.

Paragraphs and tables that don't fit their box return what's left over, so a
layout function can flow a long lease or statement onto further pages::

    rest = lease_text
    while rest:
        rest = doc.paragraph(doc.content_box, rest).rest
        if rest:
            doc.page()
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple, Optional, Sequence

LETTER = (612.0, 792.0)

Color = tuple[float, float, float]
BLACK: Color = (0.0, 0.0, 0.0)
RULE_GRAY: Color = (0.6, 0.6, 0.6)
LIGHT_GRAY: Color = (0.85, 0.85, 0.85)

# Name -> resource key. The order fixes object numbers, so it's part of the
# output format: append, never reorder.
FONTS = {"Helvetica": "F1", "Helvetica-Bold": "F2", "Courier": "F3"}

# Adobe AFM advance widths (1/1000 em) for printable ASCII, 32..126.
_HELVETICA_ASCII = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
)
_HELVETICA_BOLD_ASCII = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
)
# The non-ASCII WinAnsi glyphs a document is likely to use, as
# (Helvetica, Helvetica-Bold). Accented letters measure as their base letter
# (that's how the AFM has them), and anything else falls back to 556.
_HELVETICA_EXTRA = {
    " ": (278, 278), "•": (350, 350), "–": (556, 556), "—": (1000, 1000),
    "‘": (222, 278), "’": (222, 278), "‚": (222, 278), "“": (333, 500),
    "”": (333, 500), "„": (333, 500), "…": (1000, 1000), "°": (400, 400),
    "©": (737, 737), "®": (737, 737), "™": (1000, 1000), "€": (556, 556),
    "£": (556, 556), "¢": (556, 556), "¥": (556, 556), "§": (556, 556),
    "¶": (537, 556), "×": (584, 584), "÷": (584, 584), "±": (584, 584),
    "·": (278, 278), "µ": (556, 611), "ß": (611, 611), "½": (834, 834),
    "¼": (834, 834), "¾": (834, 834), "«": (556, 556), "»": (556, 556),
    "‹": (333, 333), "›": (333, 333), "†": (556, 556), "‡": (556, 556),
    "‰": (1000, 1000), "¡": (333, 333), "¿": (611, 611), "æ": (889, 889),
    "Æ": (1000, 1000), "ø": (611, 611), "Ø": (778, 778), "œ": (944, 944),
    "Œ": (1000, 1000), "²": (333, 333), "³": (333, 333), "¹": (333, 333),
}
_COURIER_WIDTH = 600
_FALLBACK_WIDTH = 556

_NUMERIC = re.compile(r"^[-+(]?[$€£]?\s?\d[\d,]*(\.\d+)?%?\)?$")
_PAD = 4.0  # table cell padding, each side


@dataclass(frozen=True)
class Box:
    """A region on the page: top-left corner, width, and an optional height.
    Without a height, a paragraph or table runs as long as it needs."""

    x: float
    y: float
    width: float
    height: Optional[float] = None


@dataclass(frozen=True)
class Column:
    """A table column. ``weight`` is its share of the table's width.
    ``align`` is ``"left"``, ``"right"``, ``"center"``, or ``"auto"``: right
    when every body cell in the column looks like a number or amount."""

    title: str
    weight: float = 1.0
    align: str = "auto"


class Flowed(NamedTuple):
    """Where a paragraph or table ended (``bottom``, the y below its last
    line or row) and what didn't fit (a string for paragraphs, a list of rows
    for tables; empty when everything fit)."""

    bottom: float
    rest: object


# ---------------------------------------------------------------------------
# Text: cleaning, measuring, wrapping.


def _clean(s: str, *, keep_newlines: bool = False) -> str:
    """``s`` reduced to what the WinAnsi fonts can draw (see the module
    docstring)."""
    out = []
    for ch in str(s).replace("\r\n", "\n"):
        if ch == "\n" and keep_newlines:
            out.append(ch)
        elif ch in "\t\r\n":
            out.append(" ")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("?")
        else:
            try:
                ch.encode("cp1252")
                out.append(ch)
            except UnicodeEncodeError:
                out.append("?")
    return "".join(out)


def _check_font(font: str) -> str:
    if font not in FONTS:
        raise ValueError(f"unknown font {font!r}; use one of {', '.join(FONTS)}")
    return font


def _char_width(ch: str, font: str) -> int:
    if font == "Courier":
        return _COURIER_WIDTH
    ascii_widths = _HELVETICA_BOLD_ASCII if font == "Helvetica-Bold" else _HELVETICA_ASCII
    if 32 <= ord(ch) <= 126:
        return ascii_widths[ord(ch) - 32]
    if ch in _HELVETICA_EXTRA:
        return _HELVETICA_EXTRA[ch][font == "Helvetica-Bold"]
    base = unicodedata.normalize("NFD", ch)[0]
    if 32 <= ord(base) <= 126:
        return ascii_widths[ord(base) - 32]
    return _FALLBACK_WIDTH


def text_width(s: str, font: str = "Helvetica", size: float = 10) -> float:
    """The width ``s`` is drawn at, in points, after the same cleaning
    :meth:`Document.text` applies."""
    _check_font(font)
    return sum(_char_width(ch, font) for ch in _clean(s)) * size / 1000


def _wrap(s: str, font: str, size: float, width: float) -> list[tuple[str, str]]:
    """Lines of ``s`` that fit ``width``, each paired with the string that
    joined it to the next line in the source (``" "``, ``"\\n"``, or ``""``
    after a word broken mid-way), so leftover lines can be rejoined into the
    original text."""
    lines: list[tuple[str, str]] = []
    for para in _clean(s, keep_newlines=True).split("\n"):
        current = ""
        for word in para.split():
            candidate = f"{current} {word}" if current else word
            if text_width(candidate, font, size) <= width:
                current = candidate
                continue
            if current:
                lines.append((current, " "))
            # A word wider than the line is broken by characters, at least one
            # per line so wrapping always makes progress.
            while text_width(word, font, size) > width and len(word) > 1:
                n = 1
                while n < len(word) and text_width(word[: n + 1], font, size) <= width:
                    n += 1
                lines.append((word[:n], ""))
                word = word[n:]
            current = word
        lines.append((current, "\n"))
    lines[-1] = (lines[-1][0], "")
    return lines


def wrap(s: str, font: str = "Helvetica", size: float = 10, width: float = 504) -> list[str]:
    """``s`` broken into lines no wider than ``width``. Words wrap at spaces;
    explicit newlines are kept (a blank line comes back as ``""``)."""
    _check_font(font)
    return [line for line, _ in _wrap(s, font, size, width)]


def _truncate(s: str, font: str, size: float, width: float) -> str:
    s = _clean(s)
    if text_width(s, font, size) <= width:
        return s
    while s and text_width(s + "…", font, size) > width:
        s = s[:-1]
    return s.rstrip() + "…"


# ---------------------------------------------------------------------------
# Output helpers.


def _n(v: float) -> str:
    """A number as PDF wants it: at most two decimals, no trailing zeros, and
    never ``-0``. Fixed formatting is part of the byte-identical promise."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pdf_string(s: str) -> bytes:
    raw = _clean(s).encode("cp1252")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _color(c: Color) -> str:
    return " ".join(_n(v) for v in c)


# ---------------------------------------------------------------------------


class Document:
    """A PDF being drawn, one page at a time.

    Drawing goes to the current page. :meth:`page` starts a new one, and the
    first drawing call starts the first page if nobody has.
    """

    def __init__(self, page_size: tuple[float, float] = LETTER, *, title: Optional[str] = None, margin: float = 54):
        self.width, self.height = page_size
        self.title = title
        self.margin = margin
        self._pages: list[list[bytes]] = []

    # -- pages -------------------------------------------------------------

    def page(self) -> int:
        """Start a new page and return its index."""
        self._pages.append([])
        return len(self._pages) - 1

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def content_box(self) -> Box:
        """The page inside its margins."""
        m = self.margin
        return Box(m, m, self.width - 2 * m, self.height - 2 * m)

    def _emit(self, op: str | bytes) -> None:
        if not self._pages:
            self.page()
        self._pages[-1].append(op.encode("ascii") if isinstance(op, str) else op)

    def _y(self, y: float) -> float:
        return self.height - y

    # -- drawing -----------------------------------------------------------

    def text(
        self,
        x: float,
        y: float,
        s: str,
        font: str = "Helvetica",
        size: float = 10,
        *,
        align: str = "left",
        color: Color = BLACK,
    ) -> float:
        """Draw one line of text with its baseline at ``y``. ``align`` decides
        whether ``x`` is the left edge, the right edge, or the centre.
        Returns the drawn width."""
        _check_font(font)
        width = text_width(s, font, size)
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        elif align != "left":
            raise ValueError(f"unknown align {align!r}")
        self._emit(
            f"q {_color(color)} rg BT /{FONTS[font]} {_n(size)} Tf {_n(x)} {_n(self._y(y))} Td ".encode("ascii")
            + _pdf_string(s)
            + b" Tj ET Q"
        )
        return width

    def paragraph(
        self,
        box: Box,
        s: str,
        font: str = "Helvetica",
        size: float = 10,
        leading: Optional[float] = None,
        *,
        color: Color = BLACK,
    ) -> Flowed:
        """Draw ``s`` word-wrapped to ``box.width``, starting at ``box.y``.
        Each line takes ``leading`` points (1.35 x size by default). If the
        box has a height, lines past it aren't drawn; their text comes back in
        ``rest``."""
        _check_font(font)
        leading = leading or size * 1.35
        lines = _wrap(s, font, size, box.width)
        fits = len(lines) if box.height is None else min(len(lines), math.floor(box.height / leading + 1e-9))
        # The baseline sits where the cap height is centred in the line.
        baseline = (leading - size) / 2 + size * 0.8
        for i, (line, _) in enumerate(lines[:fits]):
            if line:
                self.text(box.x, box.y + i * leading + baseline, line, font, size, color=color)
        rest = "".join(line + joiner for line, joiner in lines[fits:])
        return Flowed(box.y + fits * leading, rest)

    def table(
        self,
        box: Box,
        columns: Sequence[Column],
        rows: Sequence[Sequence[object]],
        *,
        font: str = "Helvetica",
        size: float = 9,
        row_height: Optional[float] = None,
        header: bool = True,
        row_rules: bool = True,
    ) -> Flowed:
        """Draw a table across ``box.width``, columns sized by weight. Cells
        too long for their column end in an ellipsis, so nothing crosses the
        box's right edge. The header row is bold, with a rule under it. If the
        box has a height, the rows that don't fit come back in ``rest``, ready
        to continue under a fresh header on the next page."""
        _check_font(font)
        row_height = row_height or size * 1.9
        total = sum(c.weight for c in columns)
        widths = [box.width * c.weight / total for c in columns]
        lefts = [box.x + sum(widths[:i]) for i in range(len(columns))]
        aligns = [self._column_align(c, i, rows) for i, c in enumerate(columns)]
        baseline = row_height / 2 + size * 0.35

        def draw_row(y: float, cells: Sequence[object], cell_font: str) -> None:
            for i, width in enumerate(widths):
                value = "" if i >= len(cells) or cells[i] is None else str(cells[i])
                if not value:
                    continue
                shown = _truncate(value, cell_font, size, width - 2 * _PAD)
                anchor = {
                    "left": lefts[i] + _PAD,
                    "right": lefts[i] + width - _PAD,
                    "center": lefts[i] + width / 2,
                }[aligns[i]]
                self.text(anchor, y + baseline, shown, cell_font, size, align=aligns[i])

        y = box.y
        right = box.x + box.width
        if header:
            draw_row(y, [c.title for c in columns], "Helvetica-Bold")
            y += row_height
            self.rule(y, box.x, right, weight=0.75, color=RULE_GRAY)
        for i, row in enumerate(rows):
            if box.height is not None and y + row_height > box.y + box.height + 1e-9:
                return Flowed(y, list(rows[i:]))
            draw_row(y, row, font)
            y += row_height
            if row_rules:
                self.rule(y, box.x, right, weight=0.25, color=LIGHT_GRAY)
        return Flowed(y, [])

    @staticmethod
    def _column_align(column: Column, index: int, rows: Sequence[Sequence[object]]) -> str:
        if column.align != "auto":
            if column.align not in ("left", "right", "center"):
                raise ValueError(f"unknown align {column.align!r}")
            return column.align
        cells = [str(r[index]).strip() for r in rows if index < len(r) and r[index] not in (None, "")]
        return "right" if cells and all(_NUMERIC.match(c) for c in cells) else "left"

    def rule(
        self,
        y: float,
        x0: Optional[float] = None,
        x1: Optional[float] = None,
        *,
        weight: float = 0.5,
        color: Color = RULE_GRAY,
    ) -> None:
        """A horizontal line at ``y``, margin to margin unless given ends."""
        x0 = self.margin if x0 is None else x0
        x1 = self.width - self.margin if x1 is None else x1
        yy = _n(self._y(y))
        self._emit(f"q {_color(color)} RG {_n(weight)} w {_n(x0)} {yy} m {_n(x1)} {yy} l S Q")

    def box(
        self,
        box: Box,
        *,
        fill: Optional[Color] = None,
        stroke: Optional[Color] = None,
        weight: float = 0.75,
        radius: float = 0,
    ) -> None:
        """A rectangle, filled and/or outlined, optionally with rounded
        corners. A box needs a height here."""
        if box.height is None:
            raise ValueError("box() needs a Box with a height")
        if fill is None and stroke is None:
            stroke = RULE_GRAY
        x, w, h = box.x, box.width, box.height
        bottom = self._y(box.y + h)
        ops = ["q"]
        if fill is not None:
            ops.append(f"{_color(fill)} rg")
        if stroke is not None:
            ops.append(f"{_color(stroke)} RG {_n(weight)} w")
        r = min(radius, w / 2, h / 2)
        if r > 0:
            k = r * 0.5523  # control-point distance for a quarter circle
            top, right = bottom + h, x + w
            ops.append(
                f"{_n(x + r)} {_n(bottom)} m {_n(right - r)} {_n(bottom)} l "
                f"{_n(right - r + k)} {_n(bottom)} {_n(right)} {_n(bottom + r - k)} {_n(right)} {_n(bottom + r)} c "
                f"{_n(right)} {_n(top - r)} l "
                f"{_n(right)} {_n(top - r + k)} {_n(right - r + k)} {_n(top)} {_n(right - r)} {_n(top)} c "
                f"{_n(x + r)} {_n(top)} l "
                f"{_n(x + r - k)} {_n(top)} {_n(x)} {_n(top - r + k)} {_n(x)} {_n(top - r)} c "
                f"{_n(x)} {_n(bottom + r)} l "
                f"{_n(x)} {_n(bottom + r - k)} {_n(x + r - k)} {_n(bottom)} {_n(x + r)} {_n(bottom)} c h"
            )
        else:
            ops.append(f"{_n(x)} {_n(bottom)} {_n(w)} {_n(h)} re")
        ops.append({(True, True): "B", (True, False): "f", (False, True): "S"}[(fill is not None, stroke is not None)])
        ops.append("Q")
        self._emit(" ".join(ops))

    # -- output ------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """The finished file. Calling it twice gives the same bytes."""
        if not self._pages:
            self.page()
        font_ids = {name: 4 + i for i, name in enumerate(FONTS)}
        first_page = 4 + len(FONTS)
        page_ids = [first_page + 2 * i for i in range(len(self._pages))]

        media_box = f"[0 0 {_n(self.width)} {_n(self.height)}]"
        fonts = " ".join(f"/{key} {font_ids[name]} 0 R" for name, key in FONTS.items())
        resources = f"<< /Font << {fonts} >> >>"

        info = "<< /Producer (OpenApps)"
        if self.title:
            info += f" /Title <FEFF{self.title.encode('utf-16-be').hex().upper()}>"
        info += " >>"

        objects: list[bytes] = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>".encode(),
            info.encode(),
        ]
        for name in FONTS:
            objects.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode())
        for page_id, ops in zip(page_ids, self._pages):
            content = b"\n".join(ops)
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox {media_box} /Resources {resources} "
                f"/Contents {page_id + 1} 0 R >>".encode()
            )
            objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 3 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


# ---------------------------------------------------------------------------
# Business-document pieces: the letterhead, section headings, label-over-value
# fields and footer that a bank statement and a shop receipt share.

INK = (0.12, 0.13, 0.15)
MUTED = (0.42, 0.44, 0.48)
BAND = (0.93, 0.94, 0.96)
ACCENT = (0.16, 0.33, 0.55)


def letterhead(doc: Document, title: str, lines: list[str], *, right: list[str] = ()) -> float:
    """A tinted band with the issuer on the left and reference lines on the
    right. Returns the y just below it."""
    m = doc.margin
    doc.box(Box(0, 0, doc.width, 96), fill=BAND)
    doc.text(m, 44, title, "Helvetica-Bold", 17, color=ACCENT)
    for i, line in enumerate(lines):
        doc.text(m, 62 + i * 13, line, size=9, color=MUTED)
    for i, line in enumerate(right):
        doc.text(doc.width - m, 44 + i * 13, line, size=9, align="right", color=MUTED)
    return 120


def heading(doc: Document, y: float, text: str) -> float:
    doc.text(doc.margin, y + 11, text.upper(), "Helvetica-Bold", 9, color=ACCENT)
    doc.rule(y + 16, weight=0.75, color=ACCENT)
    return y + 26


def fields(doc: Document, y: float, pairs: list[tuple[str, str]], *, columns: int = 2) -> float:
    """Label-over-value pairs in a grid, the way forms print them."""
    width = doc.content_box.width / columns
    for i, (label, value) in enumerate(pairs):
        x = doc.margin + (i % columns) * width
        row_y = y + (i // columns) * 30
        doc.text(x, row_y + 9, label, size=7.5, color=MUTED)
        doc.text(x, row_y + 22, str(value), size=10, color=INK)
    return y + ((len(pairs) + columns - 1) // columns) * 30 + 6


def footer(doc: Document, text: str) -> None:
    doc.rule(doc.height - 44, weight=0.25)
    doc.text(doc.margin, doc.height - 30, text, size=7.5, color=MUTED)
