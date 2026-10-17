#!/usr/bin/env bash
#
# build-rulebook.sh — concatenate every rulebook markdown file into one PDF.
#
# Usage:   ./build-rulebook.sh [output.pdf]
# Default output: alpha-delta-dynasty-constitution.pdf (next to this script)
#
# Requires only python3 and Google Chrome / Chromium. No packages are installed
# and nothing is downloaded; the markdown converter is embedded below.
#
# Document order: README, then the numbered documents 00-10, then the appendix,
# then DECISIONS. Any markdown file that does not match those patterns is
# appended at the end so a new document is never silently dropped.
#
# Sorting is forced to LC_ALL=C so ordering does not shift with the locale:
# under a locale-aware collation '00a-...' sorts before '00-...', which is not
# the order the README index uses.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
OUT="${1:-$HERE/alpha-delta-dynasty-constitution.pdf}"
HTML="$HERE/.rulebook.html"

# ---------------------------------------------------------------- find chrome
CHROME=""
for c in google-chrome google-chrome-stable chromium chromium-browser; do
  if command -v "$c" >/dev/null 2>&1; then CHROME="$(command -v "$c")"; break; fi
done
if [[ -z "$CHROME" ]]; then
  echo "error: no Chrome/Chromium found; cannot render PDF." >&2
  exit 1
fi

# ------------------------------------------------------------- build the list
cd "$SRC"
ORDER=()
[[ -f README.md ]] && ORDER+=("README.md")
while IFS= read -r f; do ORDER+=("$f"); done < <(ls -1 [0-9]*.md 2>/dev/null | LC_ALL=C sort)
while IFS= read -r f; do ORDER+=("$f"); done < <(ls -1 appendix*.md 2>/dev/null | LC_ALL=C sort)
[[ -f DECISIONS.md ]] && ORDER+=("DECISIONS.md")
# catch anything not matched above
while IFS= read -r f; do
  found=0
  for known in "${ORDER[@]}"; do [[ "$known" == "$f" ]] && found=1 && break; done
  if [[ $found -eq 0 ]]; then
    echo "note: including unlisted document $f at the end" >&2
    ORDER+=("$f")
  fi
done < <(ls -1 *.md 2>/dev/null | LC_ALL=C sort)

if [[ ${#ORDER[@]} -eq 0 ]]; then
  echo "error: no markdown files found in $SRC" >&2
  exit 1
fi

echo "Building PDF from ${#ORDER[@]} documents:"
printf '  %s\n' "${ORDER[@]}"

# ------------------------------------------------------- markdown -> one HTML
SRC="$SRC" HTML="$HTML" python3 - "${ORDER[@]}" <<'PYEOF'
import html as H
import os
import re
import sys
from datetime import date

SRC = os.environ["SRC"]
OUT = os.environ["HTML"]
FILES = sys.argv[1:]
KNOWN = set(FILES)


def slug(text):
    """GitHub-style heading anchor."""
    t = text.strip().lower().replace("`", "")
    t = re.sub(r"[^\w\s-]", "", t)
    return re.sub(r"\s+", "-", t.strip())


def docid(fname):
    return "doc-" + os.path.splitext(fname)[0].lower()


# --------------------------------------------------------------- inline pass
def inline(text):
    """Escape, then apply inline markdown. Code spans are protected first."""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return "\x00%d\x00" % (len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = H.escape(text, quote=False)

    # links: [label](target)
    def link(m):
        label, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "#")):
            href = target
        else:
            # internal .md reference -> in-document anchor
            path, _, anchor = target.partition("#")
            base = os.path.basename(path)
            href = ("#" + anchor) if anchor else ("#" + docid(base) if base in KNOWN else target)
        ext = ' class="ext"' if target.startswith("http") else ""
        return '<a href="%s"%s>%s</a>' % (href, ext, label)

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", text)

    for i, code in enumerate(spans):
        text = text.replace("\x00%d\x00" % i, "<code>%s</code>" % H.escape(code, quote=False))
    return text


def is_table_row(line):
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 1


def is_table_sep(line):
    s = line.strip()
    return bool(s) and set(s) <= set("|-: ") and "-" in s and s.startswith("|")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


# ---------------------------------------------------------------- block pass
def render(md, seen_ids):
    out = []
    lines = md.split("\n")
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # horizontal rule
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            anchor = slug(text)
            # keep anchors unique across the concatenated document
            if anchor in seen_ids:
                k = 2
                while "%s-%d" % (anchor, k) in seen_ids:
                    k += 1
                anchor = "%s-%d" % (anchor, k)
            seen_ids.add(anchor)
            out.append('<h%d id="%s">%s</h%d>' % (level, anchor, inline(text), level))
            i += 1
            continue

        # table
        if is_table_row(line) and i + 1 < n and is_table_sep(lines[i + 1]):
            head = cells(line)
            i += 2
            body = []
            while i < n and is_table_row(lines[i]):
                body.append(cells(lines[i]))
                i += 1
            t = ["<table><thead><tr>"]
            t += ["<th>%s</th>" % inline(c) for c in head]
            t.append("</tr></thead><tbody>")
            for row in body:
                row = (row + [""] * len(head))[: len(head)]
                t.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in row) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue

        # blockquote
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % render("\n".join(buf), seen_ids))
            continue

        # task list (must be tested before the plain bullet list)
        if re.match(r"^\s*[-*]\s+\[[ xX]\]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+\[[ xX]\]\s+", lines[i]):
                m2 = re.match(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)$", lines[i])
                box = "&#9745;" if m2.group(1).lower() == "x" else "&#9744;"
                items.append('<li class="task">%s %s</li>' % (box, inline(m2.group(2))))
                i += 1
            out.append('<ul class="tasklist">%s</ul>' % "".join(items))
            continue

        # ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            items, start = [], int(re.match(r"^\s*(\d+)\.", line).group(1))
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                text = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                i += 1
                while i < n and lines[i].startswith("   ") and lines[i].strip() \
                        and not re.match(r"^\s*\d+\.\s+", lines[i]):
                    text += " " + lines[i].strip()
                    i += 1
                items.append("<li>%s</li>" % inline(text))
            out.append('<ol start="%d">%s</ol>' % (start, "".join(items)))
            continue

        # unordered list
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]) \
                    and not re.match(r"^\s*[-*]\s+\[[ xX]\]\s+", lines[i]):
                text = re.sub(r"^\s*[-*]\s+", "", lines[i])
                i += 1
                while i < n and lines[i].startswith("  ") and lines[i].strip() \
                        and not re.match(r"^\s*[-*]\s+", lines[i]):
                    text += " " + lines[i].strip()
                    i += 1
                items.append("<li>%s</li>" % inline(text))
            out.append("<ul>%s</ul>" % "".join(items))
            continue

        # paragraph
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|\s*[-*]\s|\s*\d+\.\s|>)", lines[i]) \
                and not is_table_row(lines[i]) and not re.fullmatch(r"-{3,}", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>%s</p>" % inline(" ".join(buf)))
        else:
            i += 1

    return "\n".join(out)


# ------------------------------------------------------------------ assemble
CSS = """
@page { size: Letter; margin: 20mm 18mm 18mm 18mm; }
* { box-sizing: border-box; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt;
       line-height: 1.5; color: #1a1a1a; margin: 0; }
.doc { page-break-before: always; }
.doc:first-of-type { page-break-before: avoid; }
h1, h2, h3, h4 { font-family: Helvetica, Arial, sans-serif; line-height: 1.25;
                 page-break-after: avoid; color: #111; }
h1 { font-size: 19pt; margin: 0 0 4pt; padding-bottom: 5pt;
     border-bottom: 2.5pt solid #1a1a1a; }
h2 { font-size: 13pt; margin: 16pt 0 5pt; padding-bottom: 2pt;
     border-bottom: 0.5pt solid #bbb; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; }
p { margin: 0 0 7pt; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 8pt; padding-left: 20pt; }
li { margin-bottom: 3pt; }
ul.tasklist { list-style: none; padding-left: 4pt; }
li.task { margin-bottom: 4pt; }
a { color: #14507d; text-decoration: none; }
a.ext { text-decoration: underline; }
code { font-family: 'DejaVu Sans Mono', Menlo, Consolas, monospace;
       font-size: 9pt; background: #f2f2f0; padding: 1pt 3pt;
       border-radius: 2pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt;
        font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 0.5pt solid #c8c8c8; padding: 3.5pt 6pt; text-align: left;
         vertical-align: top; }
th { background: #ececea; font-family: Helvetica, Arial, sans-serif;
     font-size: 9pt; }
tr:nth-child(even) td { background: #fafaf8; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; background: #f7f7f4;
             border-left: 2.5pt solid #999; page-break-inside: avoid; }
blockquote p:last-child { margin-bottom: 0; }
hr { border: 0; border-top: 0.5pt solid #ccc; margin: 12pt 0; }
.title-page { text-align: center; padding-top: 55mm; page-break-after: always; }
.title-page .name { font-family: Helvetica, Arial, sans-serif; font-size: 30pt;
                    font-weight: 700; letter-spacing: -0.5pt; margin-bottom: 6pt; }
.title-page .sub { font-size: 13pt; color: #444; margin-bottom: 28pt; }
.title-page .meta { font-size: 10pt; color: #666; line-height: 1.9; }
.title-page hr { width: 55mm; margin: 22pt auto; }
.filetag { font-family: Helvetica, Arial, sans-serif; font-size: 7.5pt;
           letter-spacing: 0.6pt; text-transform: uppercase; color: #999;
           margin-bottom: 3pt; }
"""

parts = []
seen = set()
for f in FILES:
    with open(os.path.join(SRC, f), encoding="utf-8") as fh:
        md = fh.read()
    parts.append(
        '<section class="doc" id="%s"><div class="filetag">%s</div>\n%s\n</section>'
        % (docid(f), H.escape(f), render(md, seen))
    )

title = "League Constitution"
try:
    with open(os.path.join(SRC, "README.md"), encoding="utf-8") as fh:
        m = re.search(r"\*\*([^*]+)\*\*", fh.read())
        league = m.group(1) if m else "Fantasy League"
except OSError:
    league = "Fantasy League"

cover = """<div class="title-page">
  <div class="name">%s</div>
  <div class="sub">League Constitution</div>
  <hr>
  <div class="meta">
    Complete rulebook &middot; %d documents<br>
    Generated %s
  </div>
</div>""" % (H.escape(league), len(FILES), date.today().strftime("%B %-d, %Y"))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>%s — %s</title><style>%s</style></head><body>%s%s</body></html>"
        % (H.escape(league), title, CSS, cover, "\n".join(parts))
    )
print("  html: %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024))
PYEOF

# ------------------------------------------------------------ render the PDF
echo "Rendering with $(basename "$CHROME")..."
CHROME_ARGS=(
  --headless=new
  --disable-gpu
  --no-first-run
  --no-pdf-header-footer
  --run-all-compositor-stages-before-draw
  --virtual-time-budget=10000
  --print-to-pdf="$OUT"
)
if ! "$CHROME" "${CHROME_ARGS[@]}" "file://$HTML" >/dev/null 2>&1; then
  # some sandboxed environments need --no-sandbox
  "$CHROME" --no-sandbox "${CHROME_ARGS[@]}" "file://$HTML" >/dev/null 2>&1
fi

if [[ ! -s "$OUT" ]]; then
  echo "error: PDF was not produced. Intermediate HTML kept at $HTML" >&2
  exit 1
fi

rm -f "$HTML"
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
