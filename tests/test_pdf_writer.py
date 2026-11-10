"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The pure-Python PDF writer behind every seeded document, statement and receipt.

What matters is what a task leans on: the same spec gives the same bytes (so
seeded files have stable checksums and ``/files_all`` is byte-stable), the file
is structurally sound (so pdf.js renders it and its text layer is readable),
the text an agent must find is really in the page, and nothing a layout draws
spills past the margins.

The structure checks use a deliberately tiny parser below rather than a PDF
library: the writer emits uncompressed streams and a classic xref table, so
offsets and text runs can be checked with nothing but ``re``.
"""

import re

import pytest

from open_apps.pdf_writer import LETTER, Box, Column, Document, text_width, wrap

# ---------------------------------------------------------------------------
# A tiny PDF reader, just enough for the writer's own output.

TEXT_RUN = re.compile(
    rb"/(F\d) ([\d.]+) Tf ([\d.-]+) ([\d.-]+) Td \(((?:\\.|[^\\)])*)\) Tj"
)
FONT_NAMES = {b"F1": "Helvetica", b"F2": "Helvetica-Bold", b"F3": "Courier"}


def unescape(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i : i + 1] == b"\\":
            nxt = raw[i + 1 : i + 2]
            if nxt.isdigit():
                out.append(int(raw[i + 1 : i + 4], 8))
                i += 4
                continue
            out += nxt
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return out.decode("cp1252")


def streams(pdf: bytes) -> list[bytes]:
    return re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S)


def runs(pdf: bytes, page: int | None = None) -> list[dict]:
    """Every text run as {font, size, x, y (from the top), text}."""
    chosen = streams(pdf) if page is None else [streams(pdf)[page]]
    height = LETTER[1]
    return [
        {
            "font": FONT_NAMES[m.group(1)],
            "size": float(m.group(2)),
            "x": float(m.group(3)),
            "y": height - float(m.group(4)),
            "text": unescape(m.group(5)),
        }
        for stream in chosen
        for m in TEXT_RUN.finditer(stream)
    ]


def texts(pdf: bytes, page: int | None = None) -> list[str]:
    return [r["text"] for r in runs(pdf, page)]


def sample() -> bytes:
    doc = Document(title="Visit summary")
    doc.text(54, 72, "Riverside Family Medicine", font="Helvetica-Bold", size=16)
    doc.rule(84)
    doc.text(54, 110, "Blood pressure: 128/82")
    doc.paragraph(Box(54, 130, 300), "Continue current plan. " * 12)
    doc.table(
        Box(54, 260, 504),
        [Column("Date", 1), Column("Description", 3), Column("Amount", 1)],
        [["Sep 01", "Rent - Northgate Properties", "-1,850.00"], ["Sep 02", "Payroll", "3,200.00"]],
    )
    doc.box(Box(54, 400, 200, 120), fill=(0.93, 0.95, 0.98), radius=8)
    doc.page()
    doc.text(54, 72, "Page two", font="Courier")
    return doc.to_bytes()


# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_spec_same_bytes(self):
        assert sample() == sample()

    def test_no_timestamps(self):
        pdf = sample()
        assert b"CreationDate" not in pdf and b"ModDate" not in pdf


class TestStructure:
    def test_header_xref_and_trailer(self):
        pdf = sample()
        assert pdf.startswith(b"%PDF-1.4\n")
        assert pdf.rstrip().endswith(b"%%EOF")

        startxref = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1))
        assert pdf[startxref:].startswith(b"xref\n")
        first, count = map(int, re.match(rb"xref\n(\d+) (\d+)\n", pdf[startxref:]).groups())
        assert first == 0
        table = pdf[startxref:].split(b"\n", 2)[2]
        entries = [table[i * 20 : (i + 1) * 20] for i in range(count)]
        assert entries[0] == b"0000000000 65535 f \n"
        for number, entry in enumerate(entries[1:], start=1):
            assert len(entry) == 20 and entry.endswith(b" n \n")
            offset = int(entry[:10])
            assert pdf[offset:].startswith(f"{number} 0 obj".encode()), number

        trailer = pdf[pdf.rindex(b"trailer") :]
        assert f"/Size {count}".encode() in trailer
        assert b"/Root 1 0 R" in trailer

    def test_pages_are_counted(self):
        pdf = sample()
        assert b"/Count 2" in pdf
        assert len(re.findall(rb"/Type /Page\b", pdf)) == 2

    def test_stream_lengths_are_exact(self):
        pdf = sample()
        for m in re.finditer(rb"/Length (\d+) >>\nstream\n(.*?)\nendstream", pdf, re.S):
            assert int(m.group(1)) == len(m.group(2))

    def test_fonts_are_referenced_not_embedded(self):
        pdf = sample()
        for name in (b"Helvetica", b"Helvetica-Bold", b"Courier"):
            assert b"/BaseFont /" + name + b" " in pdf
        assert b"/FontFile" not in pdf
        assert b"/Encoding /WinAnsiEncoding" in pdf

    def test_empty_document_still_has_a_page(self):
        assert b"/Count 1" in Document().to_bytes()


class TestText:
    def test_text_is_in_the_content_streams(self):
        pdf = sample()
        assert "Blood pressure: 128/82" in texts(pdf, page=0)
        assert "Page two" in texts(pdf, page=1)
        assert "Rent - Northgate Properties" in texts(pdf)

    def test_title_is_in_info(self):
        pdf = sample()
        expected = "Visit summary".encode("utf-16-be").hex().upper().encode()
        assert b"/Title <FEFF" + expected + b">" in pdf

    @pytest.mark.parametrize("s", ["a(b)c", "back\\slash", "((nested))", ")("])
    def test_special_characters_are_escaped(self, s):
        doc = Document()
        doc.text(54, 72, s)
        pdf = doc.to_bytes()
        assert texts(pdf) == [s]
        assert b"(" + s.encode() + b")" not in pdf

    def test_winansi_characters_survive(self):
        doc = Document()
        doc.text(54, 72, "Café — 25 °C • €12 “ok” Größe")
        assert texts(doc.to_bytes()) == ["Café — 25 °C • €12 “ok” Größe"]

    def test_characters_outside_winansi_become_question_marks(self):
        doc = Document()
        doc.text(54, 72, "报告 ok ✓")
        assert texts(doc.to_bytes()) == ["?? ok ?"]

    def test_tabs_become_spaces_and_controls_question_marks(self):
        doc = Document()
        doc.text(54, 72, "a\tb\x00c")
        assert texts(doc.to_bytes()) == ["a b?c"]

    def test_unknown_font_is_refused(self):
        with pytest.raises(ValueError, match="Times-Roman"):
            Document().text(54, 72, "x", font="Times-Roman")

    @pytest.mark.parametrize("align", ["right", "center"])
    def test_alignment_anchors_on_x(self, align):
        doc = Document()
        width = doc.text(300, 72, "Total due", align=align)
        (run,) = runs(doc.to_bytes())
        expected = 300 - width if align == "right" else 300 - width / 2
        assert run["x"] == pytest.approx(expected, abs=0.01)


class TestMeasurement:
    def test_courier_is_monospaced(self):
        assert text_width("iiii", "Courier", 10) == text_width("MMMM", "Courier", 10) == 24

    def test_helvetica_widths_follow_the_afm(self):
        assert text_width("H", "Helvetica", 1000) == 722
        assert text_width("i", "Helvetica", 1000) == 222
        assert text_width("i", "Helvetica-Bold", 1000) == 278

    def test_accented_letters_measure_like_their_base(self):
        assert text_width("é", "Helvetica", 10) == text_width("e", "Helvetica", 10)


class TestParagraph:
    BOX = Box(72, 100, 200)
    TEXT = (
        "Your blood pressure was within the target range at this visit. "
        "Keep taking lisinopril 10 mg once daily and recheck in six months.\n\n"
        "Supercalifragilisticexpialidociouslylongidentifierwithnobreaks here."
    )

    def test_wrap_respects_the_width(self):
        for line in wrap(self.TEXT, "Helvetica", 10, 200):
            assert text_width(line, "Helvetica", 10) <= 200

    def test_explicit_newlines_are_kept(self):
        lines = wrap("one\n\ntwo", "Helvetica", 10, 200)
        assert lines == ["one", "", "two"]

    def test_lines_stay_inside_the_box(self):
        doc = Document()
        result = doc.paragraph(self.BOX, self.TEXT, size=10, leading=13)
        drawn = runs(doc.to_bytes())
        assert len(drawn) >= 4
        for run in drawn:
            assert run["x"] >= self.BOX.x
            assert run["x"] + text_width(run["text"], run["font"], run["size"]) <= self.BOX.x + self.BOX.width
            assert self.BOX.y < run["y"] <= result.bottom
        assert result.rest == ""

    def test_words_survive_wrapping(self):
        doc = Document()
        doc.paragraph(self.BOX, self.TEXT)
        joined = "".join(texts(doc.to_bytes()))
        assert joined.replace(" ", "") == self.TEXT.replace(" ", "").replace("\n", "")

    def test_overflow_is_returned_for_the_next_page(self):
        doc = Document()
        box = Box(72, 100, 200, height=30)
        result = doc.paragraph(box, self.TEXT, size=10, leading=13)
        drawn = texts(doc.to_bytes())
        assert len(drawn) == 2
        assert result.rest and result.bottom <= box.y + box.height
        assert (" ".join(drawn) + " " + result.rest).split() == self.TEXT.split()

    def test_flowing_across_pages(self):
        doc = Document()
        rest = "Lorem ipsum dolor sit amet. " * 400
        while rest:
            rest = doc.paragraph(doc.content_box, rest).rest
            if rest:
                doc.page()
        assert doc.page_count > 1
        for page in range(doc.page_count):
            for run in runs(doc.to_bytes(), page):
                assert run["y"] <= LETTER[1] - doc.margin


class TestTable:
    COLUMNS = [Column("Date", 1), Column("Description", 4), Column("Amount", 1.2), Column("Balance", 1.2)]
    ROWS = [
        ["Sep 01", "ACH debit NORTHGATE PROPERTIES LLC rent September 2026 ref 88812", "-1,850.00", "4,210.55"],
        ["Sep 02", "Payroll", "3,200.00", "7,410.55"],
        ["Sep 05", "Card 2043 Corner Grocery", "-64.18", "7,346.37"],
    ]

    def test_cells_stay_inside_the_box(self):
        doc = Document()
        box = doc.content_box
        doc.table(box, self.COLUMNS, self.ROWS)
        for run in runs(doc.to_bytes()):
            right = run["x"] + text_width(run["text"], run["font"], run["size"])
            assert box.x <= run["x"] and right <= box.x + box.width + 0.01

    def test_long_cells_are_truncated_with_an_ellipsis(self):
        doc = Document()
        doc.table(doc.content_box, self.COLUMNS, self.ROWS)
        long = [t for t in texts(doc.to_bytes()) if t.startswith("ACH debit")]
        assert long and long[0].endswith("…")

    def test_numeric_columns_are_right_aligned(self):
        doc = Document()
        doc.table(doc.content_box, self.COLUMNS, self.ROWS)
        drawn = runs(doc.to_bytes())
        amounts = [r for r in drawn if r["text"] in ("-1,850.00", "3,200.00", "-64.18")]
        rights = [r["x"] + text_width(r["text"], r["font"], r["size"]) for r in amounts]
        assert len(rights) == 3 and max(rights) - min(rights) < 0.01
        dates = [r for r in drawn if r["text"].startswith("Sep ")]
        assert len({r["x"] for r in dates}) == 1

    def test_header_is_bold_and_body_is_not(self):
        doc = Document()
        doc.table(doc.content_box, self.COLUMNS, self.ROWS)
        drawn = runs(doc.to_bytes())
        assert next(r for r in drawn if r["text"] == "Description")["font"] == "Helvetica-Bold"
        assert next(r for r in drawn if r["text"] == "Payroll")["font"] == "Helvetica"

    def test_rows_that_do_not_fit_are_returned(self):
        doc = Document()
        rows = [[f"Sep {i:02d}", "Coffee", "-4.50", "100.00"] for i in range(1, 31)]
        box = Box(54, 600, 504, height=120)
        result = doc.table(box, self.COLUMNS, rows)
        assert 0 < len(result.rest) < len(rows)
        assert result.bottom <= box.y + box.height
        drawn = [t for t in texts(doc.to_bytes()) if t.startswith("Sep ")]
        assert drawn + [r[0] for r in result.rest] == [r[0] for r in rows]

    def test_explicit_alignment_wins(self):
        doc = Document()
        doc.table(Box(54, 100, 300), [Column("Code", 1, align="left")], [["123"], ["45"]])
        assert len({r["x"] for r in runs(doc.to_bytes())}) == 1


def test_a_sample_opens_in_pdfium(tmp_path):
    """Belt and braces: a real PDF engine reads the file and its text."""
    pdfium = pytest.importorskip("pypdfium2")
    pdf = pdfium.PdfDocument(sample())
    assert len(pdf) == 2
    page = pdf[0].get_textpage().get_text_range()
    assert "Blood pressure: 128/82" in page
