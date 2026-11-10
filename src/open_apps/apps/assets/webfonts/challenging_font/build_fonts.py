"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Rebuild the fonts the `challenging_font` theme bundles. Not run at launch:
the outputs are committed, so a node with no network renders the same face
as one with it. Run by hand only to change the faces or the character set.

    uv run --no-project --with fonttools --with brotli \\
        python src/open_apps/apps/assets/webfonts/challenging_font/build_fonts.py

Inputs are the Fontsource npm packages (registry.npmjs.org; both OFL-1.1, no
Reserved Font Name, so subsetting is permitted):

* @fontsource/great-vibes 5.3.0 -- the latin and latin-ext woff2 files are
  copied as they are (``great-vibes-latin*.woff2``).
* @fontsource/zhi-mang-xing 5.3.0 -- shipped as ~90 unicode-range slices
  (2.5 MB). They are merged back into one font and subset to the 3,755
  GB2312 level-1 hanzi (the common set) plus GB2312's punctuation rows and
  every CJK character the repo's Mandarin text uses, giving one 1.2 MB file
  (``zhi-mang-xing-gb2312-l1.woff2``). A rarer character falls through the
  theme's stack to a CJK brush face the host may have, then a readable one.
"""
from __future__ import annotations

import io
import json
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[5]
NPM = "https://registry.npmjs.org/@fontsource/{name}/-/{name}-{version}.tgz"


def fetch(name: str, version: str) -> tarfile.TarFile:
    with urllib.request.urlopen(NPM.format(name=name, version=version), timeout=60) as r:
        return tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:gz")


def wanted_codepoints() -> set[int]:
    """GB2312 punctuation (rows 0xA1-0xA9) and level-1 hanzi (0xB0-0xD7),
    plus every non-Latin character in the repo's Mandarin text."""
    out: set[int] = set()
    for hi in range(0xA1, 0xF8):
        if not (hi <= 0xA9 or 0xB0 <= hi <= 0xD7):
            continue
        for lo in range(0xA1, 0xFF):
            try:
                out.add(ord(bytes([hi, lo]).decode("gb2312")))
            except UnicodeDecodeError:
                pass
    sources = [
        *REPO.glob("config/**/*mandarin*.yaml"),
        *REPO.glob("config/apps/events/*.yaml"),
        REPO / "src" / "open_apps" / "i18n.py",
    ]
    for path in sources:
        out |= {ord(c) for c in path.read_text(encoding="utf-8") if ord(c) > 0x2000}
    return out


def build_cjk(out: Path) -> None:
    from fontTools.merge import Merger
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    tar = fetch("zhi-mang-xing", "5.3.0")
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for member in tar.getmembers():
            if not member.name.endswith("-400-normal.woff2"):
                continue
            font = TTFont(io.BytesIO(tar.extractfile(member).read()))
            font.flavor = None
            # Some slices carry empty layout tables, which the merger cannot
            # combine; a CJK brush face uses no OpenType features anyway.
            for tag in ("GSUB", "GPOS", "GDEF"):
                if tag in font:
                    del font[tag]
            path = Path(tmp) / (Path(member.name).stem + ".ttf")
            font.save(path)
            paths.append(str(path))
        merged = Merger().merge(sorted(paths))
        merged_path = Path(tmp) / "merged.ttf"
        merged.save(merged_path)
        font = TTFont(merged_path)

    keep = sorted(wanted_codepoints() & set(font.getBestCmap()))
    options = Options()
    options.flavor = "woff2"
    options.layout_features = []
    options.name_IDs = ["*"]
    options.notdef_outline = True
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=keep)
    subsetter.subset(font)
    font.flavor = "woff2"
    font.save(out)
    print(f"{out.name}: {len(keep)} characters, {out.stat().st_size // 1024} KiB")


def copy_latin() -> None:
    tar = fetch("great-vibes", "5.3.0")
    for subset in ("latin", "latin-ext"):
        member = tar.getmember(f"package/files/great-vibes-{subset}-400-normal.woff2")
        (HERE / f"great-vibes-{subset}.woff2").write_bytes(tar.extractfile(member).read())
    unicode = json.loads(tar.extractfile("package/unicode.json").read())
    print("great-vibes unicode ranges (keep fonts.css in step):", json.dumps(unicode, indent=1))


if __name__ == "__main__":
    copy_latin()
    build_cjk(HERE / "zhi-mang-xing-gb2312-l1.woff2")
