"""
update_knowledge.py
───────────────────
Genererer en frisk codebase.md af hele Trading Dash projektet,
klar til upload som project knowledge i Claude.

Brug:
    python update_knowledge.py

Output: codebase.md i samme mappe som dette script.

Placering: C:\\Projects\\Trading_Dash\\update_knowledge.py
            (læg den i projektets rod, ikke i backend/)
"""

from pathlib import Path
from datetime import datetime
import sys

# ─────────────────────────────────────────────────────────────
# KONFIG — tilpas hvis du flytter scriptet
# ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent        # mappen scriptet ligger i
OUTPUT_FILE  = PROJECT_ROOT / "codebase.md"

# Filtyper der inkluderes
INCLUDE_EXTENSIONS = {
    ".py",          # Python backend
    ".tsx", ".ts",  # React/TypeScript frontend
    ".jsx", ".js",  # Plain JS (build configs osv.)
    ".html",        # Studio + index.html
    ".css",         # Tema-filer
    ".md",          # Dokumentation
    ".json",        # package.json, tsconfig.json, tauri.conf.json
    ".toml",        # Cargo.toml, pyproject.toml
}

# Specifikke filer der altid inkluderes uanset extension
ALWAYS_INCLUDE = {
    "Cargo.toml",
    "package.json",
    "tsconfig.json",
    "tauri.conf.json",
    "vite.config.ts",
    "vite.config.js",
    ".env.example",
    "requirements.txt",
    "README.md",
}

# Mapper der skippes helt (ingen filer herfra inkluderes)
SKIP_DIRS = {
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".git",
    ".vscode",
    ".idea",
    "dist",
    "build",
    "target",          # Rust build output
    ".next",
    ".cache",
    "_archive",        # backend/_archive — gamle eksperimenter
    "logs",
    "data",            # backend/data — store CSV-filer
    ".pytest_cache",
}

# Filer der skippes (uanset hvor de ligger)
SKIP_FILES = {
    "package-lock.json",   # for stor og uinteressant
    "yarn.lock",
    "Cargo.lock",
    "codebase.md",         # output-filen selv
    "update_knowledge.py", # dette script
    ".DS_Store",
    "Thumbs.db",
}

# Maks filstørrelse — filer større end dette springes over (i KB)
MAX_FILE_KB = 500

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def should_skip_dir(path: Path) -> bool:
    """Skip hele mappen hvis den er på SKIP_DIRS-listen."""
    return any(part in SKIP_DIRS for part in path.parts)


def should_include_file(path: Path) -> bool:
    """Afgør om en fil skal med i output."""
    if path.name in SKIP_FILES:
        return False
    if path.name in ALWAYS_INCLUDE:
        return True
    if path.suffix.lower() not in INCLUDE_EXTENSIONS:
        return False
    try:
        size_kb = path.stat().st_size / 1024
        if size_kb > MAX_FILE_KB:
            return False
    except OSError:
        return False
    return True


def read_file_safely(path: Path) -> str | None:
    """Læs fil med fallback-encoding. Returnerer None hvis filen ikke kan læses."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return None


def get_language_hint(path: Path) -> str:
    """Returner sprog-hint til markdown code fence."""
    ext = path.suffix.lower()
    return {
        ".py":   "python",
        ".tsx":  "tsx",
        ".ts":   "typescript",
        ".jsx":  "jsx",
        ".js":   "javascript",
        ".html": "html",
        ".css":  "css",
        ".json": "json",
        ".toml": "toml",
        ".md":   "markdown",
    }.get(ext, "")


# ─────────────────────────────────────────────────────────────
# Hovedfunktion
# ─────────────────────────────────────────────────────────────

def collect_files() -> list[Path]:
    """Walk projektet og returnér alle filer der skal med, sorteret."""
    files = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if should_skip_dir(path.relative_to(PROJECT_ROOT)):
            continue
        if not should_include_file(path):
            continue
        files.append(path)

    # Sortér: backend først, så src (frontend), så resten
    def sort_key(p: Path) -> tuple:
        rel = p.relative_to(PROJECT_ROOT)
        parts = rel.parts
        first = parts[0] if parts else ""
        priority = {
            "backend": 0,
            "src":     1,
            "src-tauri": 2,
            "studio":  3,
        }.get(first, 9)
        return (priority, str(rel).lower())

    files.sort(key=sort_key)
    return files


def write_codebase(files: list[Path]) -> tuple[int, int]:
    """Skriv codebase.md. Returnerer (antal_filer, total_kb)."""
    total_bytes = 0
    written     = 0
    skipped     = []

    with OUTPUT_FILE.open("w", encoding="utf-8") as out:
        # Header
        out.write(f"# Trading Dash — Codebase\n\n")
        out.write(f"Genereret: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"Rod: `{PROJECT_ROOT}`\n\n")
        out.write(f"---\n\n")

        # Filtræ-oversigt
        out.write(f"## Filoversigt\n\n```\n")
        for f in files:
            rel = f.relative_to(PROJECT_ROOT)
            size_kb = f.stat().st_size / 1024
            out.write(f"{rel}  ({size_kb:.1f} KB)\n")
        out.write(f"```\n\n---\n\n")

        # Fil-indhold
        for f in files:
            rel     = f.relative_to(PROJECT_ROOT)
            content = read_file_safely(f)

            if content is None:
                skipped.append(str(rel))
                continue

            lang = get_language_hint(f)

            out.write(f"## `{rel}`\n\n")
            out.write(f"```{lang}\n")
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")
            out.write(f"```\n\n")

            total_bytes += len(content.encode("utf-8"))
            written     += 1

        # Footer
        if skipped:
            out.write(f"---\n\n## Ulæselige filer (sprunget over)\n\n")
            for s in skipped:
                out.write(f"- {s}\n")

    return written, total_bytes // 1024


# ─────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n📦 Trading Dash — Knowledge Update")
    print(f"   Rod: {PROJECT_ROOT}")
    print(f"   Output: {OUTPUT_FILE.name}")
    print()

    if not PROJECT_ROOT.exists():
        print(f"❌ Rod-mappen findes ikke: {PROJECT_ROOT}")
        sys.exit(1)

    print(f"🔍 Scanner filer...")
    files = collect_files()
    print(f"   {len(files)} filer fundet\n")

    if not files:
        print(f"❌ Ingen filer at inkludere. Tjek konfig.")
        sys.exit(1)

    print(f"✍  Skriver codebase.md...")
    written, total_kb = write_codebase(files)

    output_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"\n✅ Færdig!")
    print(f"   {written} filer inkluderet")
    print(f"   Indhold: {total_kb} KB")
    print(f"   Output:  {output_kb:.1f} KB → {OUTPUT_FILE}")
    print()
    print(f"💡 Næste skridt:")
    print(f"   1. Gå til projektet i Claude.ai")
    print(f"   2. Slet eksisterende codebase.md fra project knowledge")
    print(f"   3. Upload den nye {OUTPUT_FILE.name}")
    print()