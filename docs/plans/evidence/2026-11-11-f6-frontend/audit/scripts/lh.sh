#!/bin/bash
# Lighthouse (desktop + mobile presets) against the production build served by vite preview.
set -u
S=${F6_SCRATCH:?set F6_SCRATCH}
export CHROME_PATH=$(node -e "console.log(require('playwright').chromium.executablePath())")
cd $S/tools
for preset in desktop mobile; do
  if [ $preset = desktop ]; then P="--preset=desktop"; else P="--form-factor=mobile"; fi
  ./node_modules/.bin/lighthouse http://127.0.0.1:4176/login $P --quiet \
    --chrome-flags="--headless=new --no-sandbox" \
    --only-categories=performance,accessibility,best-practices \
    --output=json --output-path=$S/lh-login-$preset.json > $S/lh-$preset.log 2>&1
  echo "$preset exit=$?"
done
