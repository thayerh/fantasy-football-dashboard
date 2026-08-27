#!/usr/bin/env bash
# Mechanical checks for the tells of an unconsidered frontend, plus the rules
# written down in design/GAMEDAY.md.
#
# These catch what review-by-eye misses on a diff. Run before declaring any
# frontend change done:  ./design/check-theme.sh
#
# Exits non-zero on any violation so it can be wired into a hook or CI.
#
# What it CANNOT check: whether the hierarchy reads, whether the blue is doing
# work or merely present, whether a real team name survives 390px. Look at it.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

SRC="frontend/src"
THEME="design/gameday.css design/athletics.css"
# Pages that claim to be built from the theme layer. Add new ones here.
PAGES="design/04-gameday.html design/07-gameday-standings.html design/08-gameday-states.html"
fail=0

report() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1"; fail=1; }
ok()     { printf '\033[0;32m✓ %s\033[0m\n' "$1"; }
warn()   { printf '\033[0;33m! %s\033[0m\n' "$1"; }
section() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

section "Theme layer (design/GAMEDAY.md)"

# T1 -- Page-local CSS. A page needing its own <style> means gameday.css is
#       missing a component; that is where the fix belongs, or the theme forks
#       one page at a time.
hits=$(grep -lnE '<style[ >]' $PAGES 2>/dev/null || true)
if [ -n "$hits" ]; then
  report "Page-local <style> block — add the component to gameday.css instead"
  echo "$hits"
else ok "No page-local CSS"; fi

# T2 -- Literal colours in the theme layer. tokens.css owns every value;
#       argyle.css is exempt because SVG data URIs cannot take a var().
hits=$(grep -nE '#[0-9a-fA-F]{3,8}\b|rgba?\(' $THEME $PAGES 2>/dev/null || true)
if [ -n "$hits" ]; then
  report "Literal colour in the theme layer — move it to tokens.css"
  echo "$hits" | head -20
else ok "Theme layer uses tokens only"; fi

# T3 -- Two type families, no third. Anything else is drift.
hits=$(grep -rhoE 'font-family:[^;]+' $THEME 2>/dev/null \
        | grep -vE '"(Oswald|Open Sans)"' || true)
if [ -n "$hits" ]; then
  report "Font family outside Oswald / Open Sans"
  echo "$hits" | sort -u
else ok "Two type families only"; fi

# T4 -- Argyle stacking. At most one loud form (field / stripe / veil) per
#       page; .argyle-mark may accompany it. Two loud forms is wallpaper.
for p in $PAGES; do
  n=$(grep -oE 'argyle-(field|stripe|veil)' "$p" 2>/dev/null | sort -u | wc -l)
  if [ "$n" -gt 1 ]; then
    report "$p uses $n loud argyle forms — one, plus .argyle-mark"
  fi
done
[ "$fail" -eq 0 ] && ok "Argyle used at one tier per page"

# T5 -- One h1 per page, naming the page. The app is named by the masthead.
for p in $PAGES; do
  n=$(grep -c '<h1' "$p" 2>/dev/null || true)
  [ "$n" -eq 1 ] || report "$p has $n <h1> — exactly one, naming the page"
done

# T6 -- Every page carries the full chrome and the three-part page header.
for p in $PAGES; do
  for need in 'class="masthead"' 'class="field' 'class="kicker"' 'class="panel"'; do
    grep -q "$need" "$p" || report "$p is missing $need"
  done
done

section "Application ($SRC)"

if [ ! -d "$SRC" ]; then
  warn "$SRC not present — skipping"
else

# 1 -- Raw colour values. Tokens are the single source of truth; a literal hex
#      in a component is how a palette silently forks.
hits=$(grep -rnE '#[0-9a-fA-F]{3,8}\b' "$SRC" --include='*.tsx' --include='*.ts' \
        --include='*.css' 2>/dev/null | grep -v 'tokens.css' || true)
if [ -n "$hits" ]; then
  report "Raw hex colours outside tokens.css — use var(--unc-*)"
  echo "$hits" | head -20
else ok "No raw hex outside tokens"; fi

# 2 -- Framework default palettes. Untouched Tailwind greys are the single
#      most recognisable signature of a generated UI.
hits=$(grep -rnE '\b(bg|text|border|ring|from|to|via)-(slate|gray|zinc|neutral|stone|indigo|violet|purple|fuchsia)-[0-9]{2,3}\b' \
        "$SRC" 2>/dev/null || true)
if [ -n "$hits" ]; then
  report "Default Tailwind palette in use — map to UNC tokens"
  echo "$hits" | head -20
else ok "No default Tailwind palettes"; fi

# 3 -- Gradients. Almost never earned, and the decorative purple-blue gradient
#      is the clearest tell of all. Gameday's only texture is argyle linework.
hits=$(grep -rnE 'bg-gradient-to|linear-gradient\(' "$SRC" 2>/dev/null || true)
if [ -n "$hits" ]; then
  report "Gradient found — flat brand colour unless there is a stated reason"
  echo "$hits" | head -10
else ok "No decorative gradients"; fi

# 4 -- Tables must be the theme's table. .stat-table brings the navy head, the
#      hairline rows and — via .num — the tabular figures that stop numeric
#      columns wobbling. A hand-rolled <table> gets none of that.
if grep -rqE '<table' "$SRC" 2>/dev/null && ! grep -rq 'stat-table' "$SRC" 2>/dev/null; then
  report "A <table> that isn't .stat-table — use the theme's table"
else
  ok "Tables use .stat-table (tabular figures included)"
fi

# 5 -- Inline styles. The app styles itself with theme classes; a style={{...}}
#      is a value that escaped tokens.css and will not follow the theme when it
#      changes.
hits=$(grep -rn 'style={{' "$SRC" 2>/dev/null || true)
if [ -n "$hits" ]; then
  report "Inline style prop — put the rule in gameday.css and use a class"
  echo "$hits" | head -10
else ok "No inline styles"; fi

# 6 -- Interface states. GAMEDAY.md requires loading, empty, and error on every
#      view that loads data. A design that only handles the happy path is
#      unfinished.
for state in 'isPending|isLoading' 'isError|catch' 'length === 0|length===0|\.length\b'; do
  if grep -rqE "$state" "$SRC" 2>/dev/null; then
    ok "State handled: $state"
  else
    report "No handling found for: $state"
  fi
done

fi

echo
if [ "$fail" -eq 0 ]; then
  printf '\033[0;32mTheme checks passed.\033[0m\n'
else
  printf '\033[1;31mTheme checks failed.\033[0m\n'
fi
exit "$fail"
