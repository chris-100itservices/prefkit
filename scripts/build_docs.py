"""Build the prefkit manual.

Steps:
  1. Generate Markdown API pages from module, class and function docstrings.
  2. Join the handwritten pages (docs/NN-*.md, in order) and the API pages into one file.
  3. If Pandoc and XeLaTeX are installed, render the manual to A4 landscape PDF.

Run from the repository root:
    python scripts/build_docs.py
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
API = DOCS / "api"
OUT = ROOT / "build" / "docs"
PACKAGE = ROOT / "prefkit"

FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _block(node: ast.AST, heading: str, depth: int) -> str:
    doc = ast.get_docstring(node)
    if doc is None:
        return ""
    return f"{'#' * depth} `{heading}`\n\n{doc}\n\n"


def generate_api_pages() -> list[Path]:
    """Write one API page per module under docs/api/. Returns the paths written."""
    if API.exists():
        shutil.rmtree(API)
    API.mkdir(parents=True)

    written: list[Path] = []
    for source in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in source.parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        module = ".".join(source.relative_to(ROOT).with_suffix("").parts)

        parts = [f"# `{module}`\n\n"]
        module_doc = ast.get_docstring(tree)
        if module_doc:
            parts.append(module_doc + "\n\n")

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                parts.append(_block(node, f"{node.name}()", depth=2))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                parts.append(_block(node, f"class {node.name}", depth=2))
                for method in node.body:
                    is_function = isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    if is_function and not method.name.startswith("_"):
                        parts.append(_block(method, f"{node.name}.{method.name}()", depth=3))

        page = API / f"{module}.md"
        page.write_text("".join(parts), encoding="utf-8")
        written.append(page)
    return written


def join_manual() -> Path:
    """Concatenate handwritten pages in filename order, then the API reference."""
    sections = []
    for page in sorted(DOCS.glob("[0-9][0-9]-*.md")):
        text = FRONT_MATTER.sub("", page.read_text(encoding="utf-8"), count=1)
        sections.append(text.strip())

    api_sections = [p.read_text(encoding="utf-8") for p in sorted(API.glob("*.md"))]
    manual = "\n\n\\newpage\n\n".join(sections + ["# API reference"] + api_sections)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "MANUAL.md"
    target.write_text(manual + "\n", encoding="utf-8")
    return target


def render_pdf(markdown: Path) -> Path | None:
    """Render with Pandoc to A4 landscape, if Pandoc and XeLaTeX are installed."""
    if shutil.which("pandoc") is None:
        print("pandoc not found: skipping PDF. The Markdown manual is ready.")
        return None

    pdf = markdown.with_suffix(".pdf")
    command = [
        "pandoc",
        str(markdown),
        "-o",
        str(pdf),
        "--pdf-engine=xelatex",
        "-V",
        "geometry:a4paper,landscape,margin=15mm",
        "-V",
        "fontsize=9pt",
        "--toc",
        "--highlight-style=tango",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return None
    return pdf


def main() -> int:
    api_pages = generate_api_pages()
    manual = join_manual()
    pdf = render_pdf(manual)

    print(f"API pages: {len(api_pages)}")
    print(f"Manual:    {manual.relative_to(ROOT)}")
    if pdf is not None:
        print(f"PDF:       {pdf.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
