#!/usr/bin/env python3
"""Fail if the studio chrome is clipped or the page overflows its viewport.

This exists because a stylesheet collision shipped to production unnoticed: `landing.css`
carried a bare `section { padding: 74px 0 }` rule and was linked into the studio, where
`.workspace` is a <section>. The rail and the inspector header were pushed out of view.
Every automated check passed, because nothing measured the rendered layout.

    python3 -m http.server 8749 --directory docs &
    python3 scripts/check_layout.py --base http://127.0.0.1:8749
"""
from __future__ import annotations

import argparse
import sys

VIEWPORTS = [(1440, 900), (1280, 800), (1100, 760), (900, 700)]

PROBE = """() => {
  const box = sel => { const el = document.querySelector(sel); if (!el) return null;
    const r = el.getBoundingClientRect();
    return {top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height)}; };
  const ws = box('.workspace');
  return {
    workspace: ws,
    rail: box('.rail'), railFirst: box('.rail-button'),
    inspector: box('.inspector'), panelHead: box('.panel.active .panel-head'),
    menubar: box('.menubar'), editor: box('#editor'),
    pageScroll: document.body.scrollHeight, viewport: innerHeight,
  }; }"""


def check(base: str) -> int:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(f"{base}/studio.html", wait_until="networkidle")
            page.click("#startBlank")
            page.wait_for_timeout(500)
            m = page.evaluate(PROBE)
            at = f"{width}x{height}"
            ws = m["workspace"]

            if m["pageScroll"] > m["viewport"] + 2:
                problems.append(f"{at}: the page scrolls as a whole "
                                f"({m['pageScroll']}px in a {m['viewport']}px viewport); "
                                "the editor pane should scroll instead")
            for name in ("rail", "inspector", "menubar", "editor"):
                if not m[name]:
                    problems.append(f"{at}: {name} is missing from the page")
            # Anything clipped above the workspace is invisible to the reader.
            for name in ("railFirst", "panelHead"):
                el = m[name]
                if el and el["top"] < ws["top"]:
                    problems.append(f"{at}: {name} is clipped, {ws['top'] - el['top']}px above the workspace")
            for name in ("rail", "inspector"):
                el = m[name]
                if el and el["h"] > ws["h"] + 2:
                    problems.append(f"{at}: {name} is {el['h'] - ws['h']}px taller than the workspace")
            page.close()
        browser.close()

    for p in problems:
        print(f"  {p}", file=sys.stderr)
    print(f"layout: {len(VIEWPORTS)} viewports checked, {len(problems)} problems")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8749")
    args = ap.parse_args()
    try:
        return check(args.base.rstrip("/"))
    except ImportError:
        print("playwright is not installed; skipping the layout check", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
