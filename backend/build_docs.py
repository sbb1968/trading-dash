#!/usr/bin/env python3
"""
build_docs.py — konverter strategi-Markdown til PDF (MD -> HTML -> Chrome print).
═══════════════════════════════════════════════════════════════════════════════════
Markdown er KILDEN (git-diffbar, redigerbar); PDF er det VISTE format. Kører på
WORKSTATION (kræver Chrome/Edge); algoserveren pull'er bare de færdige PDF'er.

Ét fælles stylesheet (docs_build/style.css) → alle PDF'er ser ens ud.

Brug:
    python build_docs.py <input.md> <output.pdf> [--title "Vist titel"]

(Massekonvertering af alle strategi-docs kommer i næste trin.)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
STYLE = (HERE / "docs_build" / "style.css").read_text(encoding="utf-8")

# Chrome/Edge — headless print-to-pdf. Første fundne bruges.
_BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# Markdown-udvidelser: tabeller + kodeblokke + pæne lister.
_MD_EXT = ["tables", "fenced_code", "sane_lists", "attr_list"]


def find_browser() -> str:
    for b in _BROWSERS:
        if Path(b).is_file():
            return b
    sys.exit("FEJL: Fandt hverken Chrome eller Edge — kan ikke generere PDF.")


def _title_from_md(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def md_to_html(md_text: str, title: str) -> str:
    body = markdown.markdown(md_text, extensions=_MD_EXT)
    return (
        '<!DOCTYPE html>\n<html lang="da"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def html_to_pdf(html: str, out_pdf: Path) -> None:
    browser = find_browser()
    out_pdf = out_pdf.resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "doc.html"
        html_path.write_text(html, encoding="utf-8")
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=3000",
                f"--print-to-pdf={out_pdf}",
                html_path.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    if not out_pdf.is_file():
        sys.exit(f"FEJL: PDF blev ikke genereret: {out_pdf}")


def convert(md_file: Path, out_pdf: Path, title: str | None = None) -> None:
    md_text = md_file.read_text(encoding="utf-8")
    title = title or _title_from_md(md_text, md_file.stem)
    html_to_pdf(md_to_html(md_text, title), out_pdf)
    print(f"OK  {md_file.name}  ->  {out_pdf.name}")


# ── Strategi-docs manifest ──────────────────────────────────────
# (strategi-mappe, docs-kategori-præfiks, [(md-version, NN, output-slug), ...]).
# MD-kilde: strategies/<mappe>/STRATEGI_<MAPPE.upper()>_<version>.md
# Output:   docs/<præfiks>_<NN>_<slug>.pdf  (titel afledes af slug i /docs/list)
_STRATEGY_DOCS = [
    ("confluence2", "k2", [
        ("kort", "01", "kort_fortalt"), ("iben", "02", "forklaring"), ("teknisk", "03", "teknisk_reference")]),
    ("europa_reversion", "eurev", [
        ("kort", "01", "kort_fortalt"), ("iben", "02", "forklaring"), ("teknisk", "03", "teknisk_reference")]),
    ("buythedip", "btd", [
        ("kort", "01", "kort_fortalt"), ("iben", "02", "forklaring"), ("teknisk", "03", "teknisk_reference")]),
    ("trend_join_long", "tjl", [
        ("kort", "01", "kort_fortalt"), ("iben", "02", "forklaring"), ("teknisk", "03", "teknisk_reference")]),
    ("relstyrke", "rs", [
        ("kort", "01", "kort_fortalt"), ("iben", "02", "forklaring"), ("teknisk", "03", "teknisk_reference")]),
]
_DOCS_OUT = HERE / "docs"

# Stand-alone docs (ikke strategi-bundet): (md-sti relativt til backend/, output-pdf-navn).
# Titel/kategori afledes af output-navnet i /docs/list (praefiks -> kategori, NN -> raekkefoelge).
_STANDALONE_DOCS = [
    ("docs_src/regime_fingeraftryk.md", "regime_01_forstaa_dit_marked.pdf"),
    ("docs_src/market_cipher_b.md", "cipher_01_hvad_viser_den.pdf"),
    ("docs_src/market_cipher_b_teknisk.md", "cipher_03_teknisk_gennemgang.pdf"),
]

# Stand-alone HTML-docs (selvstaendige sider med egen styling/SVG — IKKE MD-skabelonen).
# Renderes direkte via Chrome print-to-pdf (deres eget @page + print-color-adjust styrer siden).
_STANDALONE_HTML = [
    ("docs_src/cipher_anatomi.html", "cipher_02_anatomi.pdf"),
]


def build_all() -> None:
    n = 0
    for sdir, prefix, versions in _STRATEGY_DOCS:
        for version, nn, slug in versions:
            md = HERE / "strategies" / sdir / f"STRATEGI_{sdir.upper()}_{version}.md"
            if not md.is_file():
                print(f"SPRING OVER (mangler): {md}")
                continue
            convert(md, _DOCS_OUT / f"{prefix}_{nn}_{slug}.pdf")
            n += 1
    for md_rel, out_pdf in _STANDALONE_DOCS:
        md = HERE / md_rel
        if not md.is_file():
            print(f"SPRING OVER (mangler): {md}")
            continue
        convert(md, _DOCS_OUT / out_pdf)
        n += 1
    for html_rel, out_pdf in _STANDALONE_HTML:
        p = HERE / html_rel
        if not p.is_file():
            print(f"SPRING OVER (mangler): {p}")
            continue
        html_to_pdf(p.read_text(encoding="utf-8"), _DOCS_OUT / out_pdf)  # egen styling
        print(f"OK  {p.name}  ->  {out_pdf}")
        n += 1
    print(f"\n{n} PDF'er bygget i {_DOCS_OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Konverter strategi-Markdown til PDF (fælles skabelon)")
    ap.add_argument("input", nargs="?", help="input .md (udelades ved --all)")
    ap.add_argument("output", nargs="?", help="output .pdf (udelades ved --all)")
    ap.add_argument("--title", default=None, help="vist titel (default: første # H1)")
    ap.add_argument("--all", action="store_true", help="byg ALLE strategi-docs fra manifestet")
    a = ap.parse_args()
    if a.all:
        build_all()
    elif a.input and a.output:
        convert(Path(a.input), Path(a.output), a.title)
    else:
        ap.error("angiv <input> <output>, eller brug --all")
