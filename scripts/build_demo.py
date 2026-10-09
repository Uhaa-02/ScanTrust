"""Build the standalone live demo (no server) from web/.

Inlines engine.js and app.js into web/index.html and switches the UI to
in-browser mode. Output: docs/index.html, which GitHub Pages serves.

    python scripts/build_demo.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def build() -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    engine = (WEB / "engine.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    html = html.replace('<script src="static/engine.js"></script>',
                        f'<script>window.SCANTRUST_MODE = "local";\n{engine}</script>')
    html = html.replace('<script src="static/app.js"></script>', f"<script>\n{app}</script>")
    assert "static/" not in html, "unreplaced script tag"
    return html


if __name__ == "__main__":
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text("<!doctype html>\n<html lang=\"en\">\n" + build() + "\n</html>\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
