# Seeded Files documents

The files the Files app copies into its store at launch and on every reset.
Each content pack under `config/apps/files/content/` names one by its `source`
key and gives it a folder, a name and dates; text documents are written
straight from the pack's `body` instead.

Tasks address these files by path and score them by sha256 (`/files_all`), so
**changing a file's bytes changes what every task sees**. Keep a replacement
deterministic and check `tests/test_files.py` still passes in every pack.

| File | Pack | What it is |
| --- | --- | --- |
| `visit_aug.pdf` | default | Visit summary, Aug 12 2026: the latest medical report (BP 128/82) |
| `visit_mar.pdf` | default | Visit summary, Mar 9 2026 (BP 136/88) |
| `labs_aug.pdf` | default | Lab results, Aug 12 2026 |
| `lease.pdf` | default | Residential lease, 44 Wharf Street: $1,850.00 a month, due the 1st |
| `insurance_card.pdf` | default | Harborline Silver PPO card, member HLH 4471 0938 22 |
| `w2.pdf` | default | W-2 2025, Northwind Analytics: box 1 $84,250.00 |
| `statement_june.pdf` | default | BUS COMPLETE CHK (...5555) statement, June 2026 |
| `permit.png` | default | Residential parking permit P-2026-04417, plate ME 7HK 214 |
| `w2_draft.pdf` | default | Draft W-2 2025, seeded in Recently Deleted |
| `labs_scan.pdf` | default | Scan of the Aug 12 lab results, seeded in Recently Deleted |
| `labs_2025.pdf` | long_descriptions | Lab results, Sep 2 2025 |
| `auto_renewal.pdf` | long_descriptions | Auto policy renewal letter |
| `gym.pdf` | long_descriptions | Gym membership agreement |
| `records_request.pdf` | misleading_descriptions | Medical records request form (a decoy) |
| `lease_offer.pdf` | misleading_descriptions | Unsigned lease renewal offer (a decoy) |

They were drawn with `open_apps.pdf_writer` (and Pillow, for the permit) by
`files_app/documents.py`, which rendered them on every launch until it was
replaced by these files. To make a new one in the same style, start from that
module in git history: `git log --diff-filter=D -- src/open_apps/apps/files_app/documents.py`.
