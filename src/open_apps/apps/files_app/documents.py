"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The seeded documents, drawn from their specs in ``config/apps/files/content``.

One small layout function per ``kind``, each over :mod:`open_apps.pdf_writer`
(or Pillow, for the one photo). They take everything they print from the
spec, so a content pack can vary a value without touching code, and they
read nothing else -- no clock, no paths, no host -- because a seeded file's
checksum is part of ``/files_all`` and has to come out the same on every
launch.

The layouts are modelled on the real thing (a clinic's visit summary, a lab
report, a W-2's boxes) because an agent has to find a value *in* them: a
reading in a vitals table, a rent figure in a lease clause. A document that
read like a key-value dump would make that trivially easy.
"""
from __future__ import annotations

import io
from typing import Callable

from open_apps.pdf_writer import LETTER, Box, Column, Document

INK = (0.12, 0.13, 0.15)
MUTED = (0.42, 0.44, 0.48)
BAND = (0.93, 0.94, 0.96)
ACCENT = (0.16, 0.33, 0.55)
RED = (0.66, 0.15, 0.15)


def render(spec: dict, owner: dict) -> bytes:
    """The bytes of the file ``spec`` describes."""
    kind = spec.get("kind")
    try:
        renderer = RENDERERS[kind]
    except KeyError:
        raise ValueError(f"files seed: unknown document kind {kind!r} for {spec.get('name')!r}") from None
    return renderer(spec, owner)


# ---------------------------------------------------------------------------
# Shared pieces.


def _letterhead(doc: Document, title: str, lines: list[str], *, right: list[str] = ()) -> float:
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


def _heading(doc: Document, y: float, text: str) -> float:
    doc.text(doc.margin, y + 11, text.upper(), "Helvetica-Bold", 9, color=ACCENT)
    doc.rule(y + 16, weight=0.75, color=ACCENT)
    return y + 26


def _fields(doc: Document, y: float, pairs: list[tuple[str, str]], *, columns: int = 2) -> float:
    """Label-over-value pairs in a grid, the way forms print them."""
    width = doc.content_box.width / columns
    for i, (label, value) in enumerate(pairs):
        x = doc.margin + (i % columns) * width
        row_y = y + (i // columns) * 30
        doc.text(x, row_y + 9, label, size=7.5, color=MUTED)
        doc.text(x, row_y + 22, str(value), size=10, color=INK)
    return y + ((len(pairs) + columns - 1) // columns) * 30 + 6


def _footer(doc: Document, text: str) -> None:
    doc.rule(doc.height - 44, weight=0.25)
    doc.text(doc.margin, doc.height - 30, text, size=7.5, color=MUTED)


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


# ---------------------------------------------------------------------------
# Kinds.


def medical_report(spec: dict, owner: dict) -> bytes:
    doc = Document(LETTER, title=f"{spec['clinic']} - Visit Summary")
    y = _letterhead(
        doc,
        spec["clinic"],
        [spec["clinic_address"]],
        right=["After Visit Summary", spec["visit_date"]],
    )
    y = _fields(
        doc,
        y,
        [
            ("Patient", owner["name"]),
            ("Date of birth", owner["date_of_birth"]),
            ("Visit date", spec["visit_date"]),
            ("Provider", spec["provider"]),
            ("Reason for visit", spec["reason"]),
        ],
    )
    y = _heading(doc, y + 6, "Vitals")
    y = doc.table(
        Box(doc.margin, y, 300),
        [Column("Measurement", 2.2), Column("Result", 1.2, "right"), Column("Units", 1)],
        [list(v) for v in spec["vitals"]],
        size=9.5,
    ).bottom
    y = _heading(doc, y + 20, "Assessment")
    y = doc.paragraph(Box(doc.margin, y, doc.content_box.width), spec["assessment"], size=10).bottom
    y = _heading(doc, y + 16, "Plan")
    doc.paragraph(Box(doc.margin, y, doc.content_box.width), spec["plan"], size=10)
    _footer(doc, f"{spec['clinic']} · This summary is for the patient's records. Questions? Call the office.")
    return doc.to_bytes()


def lab_results(spec: dict, owner: dict) -> bytes:
    doc = Document(LETTER, title=f"{spec['lab']} - Results")
    y = _letterhead(doc, spec["lab"], ["Clinical Laboratory Report"], right=[f"Reported {spec['reported']}"])
    if spec.get("scanned_copy"):
        doc.text(doc.width - doc.margin, 112, "SCANNED COPY", "Helvetica-Bold", 9, align="right", color=MUTED)
    y = _fields(
        doc,
        y + 4,
        [
            ("Patient", owner["name"]),
            ("Date of birth", owner["date_of_birth"]),
            ("Ordering provider", spec["ordered_by"]),
            ("Collected", spec["collected"]),
        ],
    )
    y = _heading(doc, y + 6, "Results")
    doc.table(
        Box(doc.margin, y, doc.content_box.width),
        [Column("Test", 2.6), Column("Result", 1, "right"), Column("Units", 1),
         Column("Reference range", 1.4), Column("Flag", 0.6, "center")],
        [list(r) for r in spec["results"]],
        size=9.5,
    )
    _footer(doc, "H = above reference range, L = below. Results reviewed by the ordering provider.")
    return doc.to_bytes()


def lease(spec: dict, owner: dict) -> bytes:
    doc = Document(LETTER, title="Residential Lease Agreement")
    m, width = doc.margin, doc.content_box.width
    doc.text(doc.width / 2, 78, "RESIDENTIAL LEASE AGREEMENT", "Helvetica-Bold", 15, align="center")
    doc.text(doc.width / 2, 96, spec["premises"], size=10, align="center", color=MUTED)
    y = _fields(
        doc,
        124,
        [
            ("Landlord", spec["landlord"]),
            ("Tenant", owner["name"]),
            ("Term", spec["term"]),
            ("Monthly rent", spec["monthly_rent"]),
            ("Rent due", spec["due_day"]),
            ("Security deposit", spec["security_deposit"]),
        ],
    )
    terms = [
        ("Rent", f"Tenant agrees to pay {spec['monthly_rent']} per month, due on {spec['due_day']}. "
                 f"A late fee of {spec['late_fee']}. {spec['payment_method']}"),
        *[(str(title), str(body)) for title, body in spec["clauses"]],
        ("Notices", f"Notices to the Landlord go to {spec['landlord_contact']}."),
    ]
    y = _heading(doc, y + 6, "Terms")
    bottom = doc.height - 60
    for n, (title, body) in enumerate(terms, start=1):
        rest = f"{n}. {title}. {body}"
        while rest:
            flowed = doc.paragraph(Box(m, y, width, bottom - y), rest, size=10, leading=14)
            rest, y = flowed.rest, flowed.bottom + 8
            if rest:
                doc.page()
                y = m
    if y > bottom - 90:
        doc.page()
        y = m
    y += 24
    for i, (who, name) in enumerate([("Landlord", spec["landlord"]), ("Tenant", owner["name"])]):
        x = m + i * (width / 2)
        doc.rule(y + 28, x, x + width / 2 - 24, weight=0.5, color=INK)
        doc.text(x, y + 40, f"{who}: {name}", size=9, color=MUTED)
        doc.text(x, y + 52, f"Signed {spec['signed']}", size=9, color=MUTED)
    return doc.to_bytes()


def insurance_card(spec: dict, owner: dict) -> bytes:
    # A wallet card is 3.375 x 2.125 in.
    doc = Document((243.0, 153.0), title=f"{spec['insurer']} member card", margin=12)
    doc.box(Box(0, 0, 243, 34), fill=ACCENT)
    doc.text(12, 22, spec["insurer"], "Helvetica-Bold", 12, color=(1, 1, 1))
    doc.text(231, 22, spec["plan"], size=7, align="right", color=(1, 1, 1))
    doc.text(12, 50, "Member", size=6, color=MUTED)
    doc.text(12, 61, owner["name"], "Helvetica-Bold", 9)
    doc.text(12, 76, "Member ID", size=6, color=MUTED)
    doc.text(12, 87, spec["member_id"], "Courier", 9)
    doc.text(150, 76, "Group", size=6, color=MUTED)
    doc.text(150, 87, spec["group"], "Courier", 9)
    doc.text(196, 76, "RxBIN", size=6, color=MUTED)
    doc.text(196, 87, spec["rx_bin"], "Courier", 9)
    for i, (label, amount) in enumerate(spec["copays"]):
        x = 12 + i * 57
        doc.text(x, 106, label, size=5.5, color=MUTED)
        doc.text(x, 116, amount, "Helvetica-Bold", 8)
    doc.text(12, 140, f"Member services {spec['member_services']}", size=6, color=MUTED)
    return doc.to_bytes()


def w2(spec: dict, owner: dict) -> bytes:
    doc = Document(LETTER, title=f"Form W-2 {spec['tax_year']}")
    m, width = doc.margin, doc.content_box.width
    doc.text(m, 70, "Form W-2", "Helvetica-Bold", 16)
    doc.text(m + 88, 70, "Wage and Tax Statement", size=12)
    doc.text(doc.width - m, 70, spec["tax_year"], "Helvetica-Bold", 22, align="right")
    if spec.get("watermark"):
        doc.text(doc.width / 2, 94, spec["watermark"], "Helvetica-Bold", 12, align="center", color=RED)
    y = 110
    left = [
        ("a  Employee's social security number", spec["employee_ssn"]),
        ("b  Employer identification number (EIN)", spec["employer_ein"]),
        ("c  Employer's name and address", f"{spec['employer']}, {spec['employer_address']}"),
        ("e  Employee's name", owner["name"]),
        ("f  Employee's address", f"{owner['street']}, {owner['city']}"),
    ]
    for label, value in left:
        doc.box(Box(m, y, width, 34), stroke=INK, weight=0.5)
        doc.text(m + 6, y + 11, label, size=7, color=MUTED)
        doc.text(m + 6, y + 26, value, size=10)
        y += 34
    y += 14
    half = width / 2
    for i, (number, label, amount) in enumerate(spec["boxes"]):
        x = m + (i % 2) * half
        row_y = y + (i // 2) * 34
        doc.box(Box(x, row_y, half, 34), stroke=INK, weight=0.5)
        doc.text(x + 6, row_y + 11, f"{number}  {label}", size=7, color=MUTED)
        doc.text(x + half - 8, row_y + 27, str(amount), "Courier", 10, align="right")
    _footer(doc, "Copy B - To Be Filed With Employee's FEDERAL Tax Return.")
    return doc.to_bytes()


def bank_statement(spec: dict, owner: dict) -> bytes:
    doc = Document(LETTER, title=f"{spec['account']} statement")
    m, width = doc.margin, doc.content_box.width
    y = _letterhead(doc, spec["bank"], [spec["account"]], right=["Account statement", spec["period"]])
    rows, balance = [], float(spec["opening_balance"])
    credits = debits = 0.0
    for date, description, amount in spec["rows"]:
        amount = float(amount)
        balance = round(balance + amount, 2)
        if amount >= 0:
            credits += amount
        else:
            debits += amount
        rows.append([date, description, _money(amount), _money(balance)])
    y = _fields(
        doc,
        y,
        [
            ("Account holder", spec["holder"]),
            ("Statement period", spec["period"]),
            ("Opening balance", _money(float(spec["opening_balance"]))),
            ("Deposits and credits", _money(round(credits, 2))),
            ("Withdrawals and debits", _money(round(debits, 2))),
            ("Closing balance", _money(balance)),
        ],
        columns=3,
    )
    if spec.get("card_summary"):
        # A card statement also prints the cycle's figures -- what is owed and
        # by when -- under the balances. Absent on deposit accounts and on the
        # seeded June statement, whose bytes must not change.
        y = _fields(doc, y, [(str(k), str(v)) for k, v in spec["card_summary"]], columns=3)
    y = _heading(doc, y + 6, "Transactions")
    rest = rows
    columns = [Column("Date", 0.8), Column("Description", 3.4), Column("Amount", 1.1, "right"),
               Column("Balance", 1.1, "right")]
    while rest:
        flowed = doc.table(Box(m, y, width, doc.height - 60 - y), columns, rest, size=9)
        rest = flowed.rest
        if rest:
            doc.page()
            y = m
    _footer(doc, f"{spec['bank']} · Member FDIC · Questions about this statement? Call 1-800-555-5555.")
    return doc.to_bytes()


def receipt(spec: dict, owner: dict) -> bytes:
    """An order receipt, as the shop emails one: who it ships to, what was
    bought, what it cost, and which card paid. Amounts arrive formatted in
    the shop's currency, so the receipt prints exactly what the Orders page
    shows."""
    doc = Document(LETTER, title=f"{spec['shop']} order {spec['order_id']}")
    m, width = doc.margin, doc.content_box.width
    y = _letterhead(
        doc,
        spec["shop"],
        ["Order receipt"],
        right=[f"Order {spec['order_id']}", spec["placed"]],
    )
    pairs = [
        ("Ship to", spec["name"] or "-"),
        ("Address", spec["address"] or "-"),
        ("Order placed", spec["placed"]),
        ("Status", spec["status"]),
    ]
    if spec.get("card_last4"):
        pairs.append(("Paid with", f"Card ending {spec['card_last4']}"))
    y = _fields(doc, y, pairs)
    y = _heading(doc, y + 6, "Items")
    columns = [Column("Item", 4.2), Column("Qty", 0.6, "right"), Column("Price", 1.1, "right"),
               Column("Total", 1.1, "right")]
    rest = [list(line) for line in spec["lines"]]
    while rest:
        flowed = doc.table(Box(m, y, width, doc.height - 120 - y), columns, rest, size=9.5)
        rest, y = flowed.rest, flowed.bottom
        if rest:
            doc.page()
            y = m
    doc.rule(y + 10, m + width * 0.55, m + width, weight=0.75, color=INK)
    doc.text(m + width * 0.55, y + 28, "Order total", "Helvetica-Bold", 11)
    doc.text(m + width, y + 28, spec["total"], "Helvetica-Bold", 11, align="right")
    _footer(doc, f"{spec['shop']} · Thank you for your order. Keep this receipt for returns.")
    return doc.to_bytes()


def permit_image(spec: dict, owner: dict) -> bytes:
    """A phone photo of a windshield permit: card on a dark dash, drawn with
    the font Pillow ships with, so no host font can change the pixels."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (960, 600), (38, 40, 44))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=28)
    big = ImageFont.load_default(size=52)
    small = ImageFont.load_default(size=22)
    draw.rounded_rectangle((90, 70, 870, 530), radius=28, fill=(250, 248, 240))
    draw.rounded_rectangle((90, 70, 870, 170), radius=28, fill=(30, 84, 140))
    draw.rectangle((90, 140, 870, 170), fill=(30, 84, 140))
    draw.text((130, 98), spec["issuer"].upper(), font=font, fill=(255, 255, 255))
    draw.text((130, 200), spec["title"], font=big, fill=(30, 84, 140))
    draw.text((130, 280), spec["zone"], font=font, fill=(40, 40, 40))
    draw.text((130, 330), f"Permit {spec['permit_number']}", font=font, fill=(40, 40, 40))
    draw.text((130, 380), f"Plate {spec['plate']}", font=font, fill=(40, 40, 40))
    draw.text((130, 460), spec["valid"], font=small, fill=(90, 90, 90))
    draw.ellipse((690, 300, 830, 440), outline=(30, 84, 140), width=8)
    draw.text((728, 345), "26", font=big, fill=(30, 84, 140))
    out = io.BytesIO()
    # No metadata chunks and fixed settings: the same pixels give the same file.
    img.save(out, format="PNG", optimize=False, compress_level=9)
    return out.getvalue()


def letter(spec: dict, owner: dict) -> bytes:
    """A one-page letter: notices, offers, forms. The content packs use it
    for noise and decoys, which must never print a vitals table -- that would
    compete with the seeded visit summaries."""
    doc = Document(LETTER, title=spec["subject"])
    m, width = doc.margin, doc.content_box.width
    y = _letterhead(doc, spec["sender"], [spec["sender_address"]], right=[spec["date"]])
    for i, line in enumerate([owner["name"], owner["street"], owner["city"]]):
        doc.text(m, y + 10 + i * 13, line, size=10)
    y = y + 64
    doc.text(m, y, spec["subject"], "Helvetica-Bold", 12)
    y += 18
    for para in spec["paragraphs"]:
        y = doc.paragraph(Box(m, y, width, doc.height - 80 - y), str(para), size=10.5, leading=15).bottom + 10
    doc.text(m, y + 14, spec.get("signoff", "Sincerely,"), size=10.5)
    doc.text(m, y + 30, spec["sender"], size=10.5)
    return doc.to_bytes()


def text(spec: dict, owner: dict) -> bytes:
    return str(spec["body"]).encode("utf-8")


RENDERERS: dict[str, Callable[[dict, dict], bytes]] = {
    "medical_report": medical_report,
    "lab_results": lab_results,
    "lease": lease,
    "insurance_card": insurance_card,
    "w2": w2,
    "bank_statement": bank_statement,
    "receipt": receipt,
    "letter": letter,
    "permit_image": permit_image,
    "text": text,
}
