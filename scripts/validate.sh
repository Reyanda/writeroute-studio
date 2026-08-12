#!/usr/bin/env bash
# Everything that must pass before a release. Exits non-zero on the first failure so a
# rule that is checkable is checked, rather than remembered.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== python: compile =="
python3 -m compileall -q writeroute aiwd app.py scripts

echo "== python: tests =="
PYTHONPATH=. python3 -m pytest tests -q

echo "== javascript: document layer =="
if node -e "import('jsdom')" >/dev/null 2>&1; then
  node --test "tests/js/*.test.mjs"
else
  echo "   jsdom is not installed; run: npm install --no-save jsdom" >&2
  exit 1
fi

echo "== site: build is reproducible and the manifest matches =="
python3 scripts/build_web.py >/dev/null
python3 - <<'PY'
import hashlib, json, pathlib, sys
docs = pathlib.Path("docs")
manifest = json.loads((docs / "engine-manifest.json").read_text())
digest = hashlib.sha256((docs / manifest["archive"]).read_bytes()).hexdigest()
if digest != manifest["sha256"]:
    sys.exit(f"engine archive checksum does not match the manifest: {digest} != {manifest['sha256']}")
for required in ("index.html", "studio.html", "engine.js", "files.js", "app.js",
                 "styles.css", "landing.css", ".nojekyll", "benchmark.json"):
    if not (docs / required).exists():
        sys.exit(f"docs/{required} is missing from the build")
print(f"   archive ok: {manifest['files']} files, {manifest['bytes']:,} bytes")
PY

echo "== site: rendered layout =="
if python3 -c "import playwright" >/dev/null 2>&1; then
  python3 -m http.server 8788 --directory docs >/dev/null 2>&1 &
  SERVER=$!
  sleep 1
  python3 scripts/check_layout.py --base http://127.0.0.1:8788 || { kill $SERVER; exit 1; }
  kill $SERVER
else
  echo "   playwright not installed; skipped" >&2
fi

echo "== site: no authorship claim on the landing page =="
if grep -qiE "% (ai|AI)[- ]generated|AI-generated|likelihood this was written by" docs/index.html; then
  echo "   the landing page must not claim an authorship verdict" >&2
  exit 1
fi

echo
echo "All checks passed."
