#!/bin/bash
# DESIGN.md tokens → static/tokens.css (CSS custom properties on :root). Run after editing DESIGN.md:
#   tools/design_tokens.sh            (lints first; needs Node for npx)
set -euo pipefail
cd "$(dirname "$0")/.."
npx -y @google/design.md lint DESIGN.md > /dev/null
{
  echo "/* Generated from DESIGN.md by tools/design_tokens.sh. Do not edit; change DESIGN.md and run the script. */"
  npx -y @google/design.md export --format css-tailwind DESIGN.md 2>/dev/null | sed 's/^@theme {/:root {/'
} > static/tokens.css
echo "wrote static/tokens.css ($(grep -c -- '--' static/tokens.css) tokens)"
