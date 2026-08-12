#!/usr/bin/env python3
"""Capture the studio and landing screenshots that the site ships.

The images in `assets/` are evidence, not decoration: the landing page uses them to show
what the tool actually reports. Stale screenshots from an older build would be a quiet
misrepresentation, so they are regenerated from the real page rather than edited.

The sample document is chosen to demonstrate the allow-list in the same frame as the
findings: "Improved water sources" is a WHO/JMP label and must be left alone, while
"studies show" and "robust framework" in the next paragraph must be flagged. A screenshot
that did not show both would be showing less than the tool does.

Requires a local server for `docs/` and Playwright's Chromium:

    python3 scripts/build_web.py
    python3 -m http.server 8751 --directory docs &
    python3 scripts/capture_screenshots.py --base http://127.0.0.1:8751
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

SAMPLE = """Improved water sources included piped water, boreholes and protected springs.

It is important to note that our robust framework leverages best practices to deliver value. Moreover, studies show that the intervention improved survival across all 240 children.

Officials must report within 30 days. The results were statistically significant (p<0.05)."""

SETUP = """
async (sample) => {
  document.getElementById('startBlank').click();
  const ed = document.getElementById('editor');
  ed.textContent = sample;
  ed.dispatchEvent(new Event('input'));
  document.getElementById('docTitle').value = 'Cohort analysis \\u2014 draft 3';
  document.getElementById('genreSelect').value = 'scientific';
  const WR = (await import('./engine.js')).default;
  await WR.ensure();
  document.getElementById('auditButton').click();
  await new Promise(r => setTimeout(r, 2000));
  return document.getElementById('scoreLabel').textContent;
}
"""


def capture(base: str, width: int, height: int) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for theme in ("light", "dark"):
                page = browser.new_page(viewport={"width": width, "height": height},
                                        device_scale_factor=2)
                page.add_init_script(
                    f"localStorage.setItem('writeroute-theme', '{theme}');")

                page.goto(f"{base}/studio.html", wait_until="networkidle")
                verdict = page.evaluate(SETUP, SAMPLE)
                if verdict in ("", "Not analysed"):
                    # A screenshot of an un-run audit would show an empty panel and
                    # misrepresent the product, so fail rather than ship it.
                    raise SystemExit(f"the audit did not render for the {theme} shot")
                suffix = "" if theme == "light" else "-dark"
                page.screenshot(path=str(ASSETS / f"workspace{suffix}.png"))
                print(f"workspace{suffix}.png  ({verdict})")

                page.goto(f"{base}/", wait_until="networkidle")
                page.screenshot(path=str(ASSETS / f"hero{suffix}.png"))
                print(f"hero{suffix}.png")
                page.close()
        finally:
            browser.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8751")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=940)
    args = ap.parse_args()
    return capture(args.base.rstrip("/"), args.width, args.height)


if __name__ == "__main__":
    raise SystemExit(main())
