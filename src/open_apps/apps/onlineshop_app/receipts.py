"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The order receipt a checkout saves into Files, laid out on
:mod:`open_apps.pdf_writer`. Kept out of ``main.py`` so the shop's handlers
read on their own; the spec it takes is built there (``receipt_pdf``).
"""
from open_apps.pdf_writer import INK, LETTER, Box, Column, Document, fields, footer, heading, letterhead


def render(spec: dict) -> bytes:
    """An order receipt, as the shop emails one: who it ships to, what was
    bought, what it cost, and which card paid. Amounts arrive formatted in
    the shop's currency, so the receipt prints exactly what the Orders page
    shows."""
    doc = Document(LETTER, title=f"{spec['shop']} order {spec['order_id']}")
    m, width = doc.margin, doc.content_box.width
    y = letterhead(
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
    y = fields(doc, y, pairs)
    y = heading(doc, y + 6, "Items")
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
    footer(doc, f"{spec['shop']} · Thank you for your order. Keep this receipt for returns.")
    return doc.to_bytes()
