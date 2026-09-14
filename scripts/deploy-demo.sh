#!/usr/bin/env bash
# Rebuild the static demo (Pyodide, no backend) and publish it to github.com/aptsalt/stratum-demo (GitHub Pages).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="${SITE:-$ROOT/../stratum-demo-site}"

(cd "$ROOT/frontend" && npm run build:demo)
[ -d "$SITE/.git" ] || git clone https://github.com/aptsalt/stratum-demo.git "$SITE"
find "$SITE" -mindepth 1 -maxdepth 1 ! -name .git ! -name README.md -exec rm -rf {} +
cp -r "$ROOT/frontend/dist/demo/browser/." "$SITE/"
cp "$SITE/index.html" "$SITE/404.html"   # SPA deep links (/stratum-demo/learn)
touch "$SITE/.nojekyll"
cd "$SITE"
git add -A
git commit -m "deploy: stratum $(git -C "$ROOT" rev-parse --short HEAD)" || echo "nothing to deploy"
git push
echo "https://aptsalt.github.io/stratum-demo/"
