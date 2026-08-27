# Gameday — the theme rules

The chosen direction, written down. `design/gameday.css` is the executable half
of this document; if the two ever disagree, the CSS is what ships and this file
is the bug.

**One sentence:** a Carolina Blue field carries the brand and the page title,
every readable thing sits on a white panel, and the athletics masthead is
identical on every page.

Live pages built entirely from these rules — no page-local CSS in any of them:

| Page | Proves |
|---|---|
| `04-gameday.html` | card grid, avatars, tags |
| `07-gameday-standings.html` | dense numeric table, stat strip, stale header |
| `08-gameday-states.html` | loading / empty / error |

---

## 0. Load order

Fixed, and every page loads all four:

```html
<link rel="stylesheet" href="tokens.css">     <!-- palette, scales -->
<link rel="stylesheet" href="argyle.css">     <!-- the four argyle forms -->
<link rel="stylesheet" href="athletics.css">  <!-- masthead, sec-head, btn -->
<link rel="stylesheet" href="gameday.css">    <!-- page shell + components -->
```

The React app loads the same four files — `frontend/src/index.css` imports them
straight out of `design/`, so there is one copy of the theme, not two. It adds
`frontend/src/styles/fonts.css`, which self-hosts Oswald and Open Sans instead
of linking Google's CDN (the mockups here still link it; they are a scratchpad,
not the deployed site).

A page that needs a `<style>` block has found a gap in `gameday.css`. Fill the
gap there; do not patch it locally. That rule is the whole difference between a
theme and a pile of pages that happen to look similar.

---

## 1. Colour

### Roles, not names

Reach for the role token. The raw `--unc-*` values exist so `tokens.css` can
define the roles; components use the role.

| Need | Token | Value |
|---|---|---|
| Page field | `--unc-carolina` | `#7BAFD4` |
| Panel surface | `--surface-raised` | white |
| Body text | `--text-strong` | navy `#13294B` |
| Secondary text | `--text-muted` | Old Well Basin `#5B6670` |
| Links on white | `--text-link` | Bolin Creek `#2C5080` |
| Text on navy | `--text-on-navy` / `--text-accent-dark` | white / Carolina |
| Hairlines | `--unc-franklin-fog` | `#ECEDED` |
| Hover wash | `--unc-cloud` | `#EDF5FB` |
| Success / warn / error | `--status-ok` / `--status-warn` / `--status-error` | pine / poplar / clay |

### The load-bearing rule

> **On light backgrounds Carolina Blue is a surface, never text — at any
> size. On navy it becomes a legitimate text colour, and a strong one.**

The blue here is `#7BAFD4`, the lighter historical value, chosen over UNC's
current published `#4B9CD3` because it looks right to this league. That choice
moves every ratio, so the rule above is stricter than the usual UNC advice:
this blue does not clear the 3:1 non-text threshold on white either.

Measured, not assumed:

| Foreground | On | Ratio | Verdict |
|---|---|---:|---|
| Carolina Blue | White | 2.35 | **fails all text, and non-text too** |
| Carolina Blue | Cloud | 2.14 | **fails everything** |
| Carolina Blue | Navy | 6.17 | passes body text comfortably |
| Navy | Carolina Blue | 6.17 | passes — why the utility nav is navy |
| White | Carolina Blue | 2.35 | display type only — see below |
| Navy | White | 14.52 | passes |
| Bolin Creek | White | 8.18 | passes |
| Old Well Basin | White | 5.87 | passes |
| Clay | White | 3.57 | error *headlines* only (Oswald 600 ≥ 19px) |
| Flat Gray | White | 2.36 | fails — borders only, never text |

**The one knowing exception**: white type on the blue field sits at 2.35. The
`<h1>` is defensible at 49px/700 as the league's identity. The `.kicker` and
`.subline` under it are not — they are small text below every threshold. Navy
would score 6.17. This is recorded rather than quietly fixed because it changes
how the header reads.

This is also how the theme hits UNC's published ~40% Carolina Blue / 15% navy /
25% white balance without shipping unreadable type: the blue is spent on the
field, the utility bar, and fills — areas, not letters.

### Forbidden

- Any literal hex outside `tokens.css`.
- Gradients. Flat brand colour, always. (The one texture is argyle, and it is
  linework, not a fade.)
- Carolina Blue text on white or cloud.
- Flat Gray or Cornerstone as text.
- A default framework palette (`slate-`, `gray-`, `zinc-`, `indigo-`…). These
  are the single most recognisable signature of an unconsidered UI.
- More than one supporting colour (Ginkgo, Clay, Poplar, Pine) visible at once
  in the same panel. They are 5% of the palette and they buy attention only
  while they stay rare.

---

## 2. Headers

### The masthead is not a per-page decision

Two tiers, byte-identical on every route:

1. **Utility bar** — Carolina Blue, holds the white chevron plate (angled right
   edge; a plain rectangle throws away the entire athletics reference) and the
   secondary nav in **navy** Oswald caps at `--step--1`, `0.08em` tracking.
   Navy on blue is 6.17:1; white on blue would be 2.35:1 and fail.

   The plate carries the league mark as an image (`design/helmet.png`, keyed to
   transparency), sized by height — `44px`, `34px` below `48rem` — so the
   plate's own padding sets the bar height. It clears the angled edge by
   `--space-7` of right padding; anything less and the mark sits under the
   slope. `alt=""`, because the league is named by the `<h1>` directly below.
2. **Section bar** — navy, holds the Carolina Blue Oswald wordmark at
   `--step-3` and the primary nav in white Open Sans 600. The current page is
   marked `aria-current="page"` and underlined in Carolina Blue.

Below `48rem` the utility nav hides and the section nav scrolls horizontally.
It never wraps to a second line.

### Page header — three parts, one order

Every page fills all three. An `<h1>` on its own floats.

```html
<div class="field-head">
  <div>
    <p class="kicker">Sleeper · 2026 Season</p>   <!-- context -->
    <h1>Delta Sig</h1>                            <!-- identity -->
    <p class="subline"><b>12</b> teams · Draft <b>pending</b></p>  <!-- facts -->
  </div>
  <span class="freshness">…</span>                <!-- data age, right -->
</div>
```

### Heading ladder

| Level | Class / element | Face | Size | Treatment |
|---|---|---|---|---|
| Page | `.field-head h1` | Oswald 700 | `--step-5` | caps, `0.95` leading, hard navy offset shadow, white on the field |
| Section | `.sec-head h2` | Oswald 700 | `--step-2` | caps, `0.02em`, navy, preceded by `.argyle-mark` |
| Sub / state | `.state h3` | Oswald 600 | `--step-1` | caps, `0.04em`, navy |
| Kicker | `.kicker` | Oswald 600 | `--step--1` | caps, `0.18em` — the widest tracking in the system |

Exactly one `<h1>` per page, and it names the page, not the app. The app is
named by the masthead.

Sizes come from the 1.25 scale in `tokens.css` (`--step--1` … `--step-5`). A
one-off `font-size: 22px` is the second most common tell after stock greys.

---

## 3. Fonts

Two families. No third one, ever.

| Family | Job | Weights |
|---|---|---|
| **Oswald** | display voice: wordmarks, headings, kickers, buttons, team names, figures, table headers, tags | 500, 600, 700 |
| **Open Sans** | everything a person reads in sentences: body, handles, notes, table cells, nav in the section bar | 400, 600, 700 |

Oswald is a heavy condensed grotesque standing in for UNC athletics' own
headline face — it carries the stadium-signage voice. It is a **voice, not a
text face**:

- Never set a paragraph, a sentence of help text, or a table cell in Oswald.
- Oswald is uppercase in every role except `.team` and `.cell-team .name`,
  where names are user data and must keep the casing the owner typed.
- Tracking is a function of size: `0.18em` for kickers, `0.12em` for tags,
  `0.08em` for nav and table headers, `0.06em` for buttons, `0.01–0.02em` for
  headings. Large display type gets less, small caps get more. Never `0` on
  uppercase Oswald and never a positive value on lowercase text.
- Any figure a reader compares down a column gets
  `font-variant-numeric: tabular-nums` — `.stat-table .num`, `.stat .figure`,
  `.rank`, and `.subline b` already do.

Absent data is styled as absent: `.unset` drops to italic Open Sans in Old Well
Basin. Never invent a placeholder that looks like real content — and never set
one in Flat Gray, which is 2.36:1 on white.

**In the app**, self-host both families rather than linking Google Fonts. The
mockups link the CDN for convenience; the deployed site should not make a
third-party request per visitor, and Cloudflare Access sits in front of it
anyway.

---

## 4. Layout and spacing

- One measure: `.wrap` at `66rem`, or `.wrap--wide` at `76rem` for pages with a
  rail. Nothing sets its own max-width, which is why panels line up across
  routes.
- Two-column pages use `.layout` — a `15.5rem` rail beside the primary column.
  The rail is secondary by definition, so below `60rem` it moves *below* the
  main column rather than pushing it off the first screen.
- The page body is `.field` (add `.field--tight` for interior pages).
- **All content lives in a `.panel`.** There is no readable text directly on
  the blue.
- Spacing comes from the 4px scale (`--space-1` … `--space-8`). `--space-5`
  (1rem) is the standard panel padding; `--space-4` below `48rem`.
- Radius varies by role and only these three exist: `--radius-sm` (2px: tags,
  buttons, skeleton lines), `--radius-md` (4px: panels), `--radius-pill`
  (avatars, freshness). Uniform rounding on everything flattens hierarchy.
- Hairlines are a 1px grid gap over `--unc-franklin-fog`, not per-element
  borders — no doubled rules, no dangling edge on the last row.
- Elevation is navy-tinted (`--shadow-sm`, `--shadow-md`), never neutral black.
  Panels get `--shadow-md` and nothing else is raised.
- Wide tables scroll inside `.table-scroll`. The page never scrolls sideways.

---

## 5. Argyle

Four forms, ranked by loudness. **A page uses at most one of the top three,
plus `.argyle-mark`.**

| Form | Where |
|---|---|
| `.argyle-field` | full lattice — reserved; too loud for a content page |
| `.argyle-stripe` | uniform side stripe — reserved |
| `.argyle-veil` | **Gameday's standard**: white linework at 20% on the blue field |
| `.argyle-mark` | the diamond cluster before every `.sec-head h2` |

The mark is the default and is essentially free: unmistakably Carolina, zero
cost to legibility, survives on a dense page. It is `aria-hidden`.

Diamonds are 3:4, taller than wide. Equilateral diamonds read as harlequin, not
argyle. The overstitch runs *parallel to the diamond edges* — arbitrary 45°
lines fragment the lattice into speckle.

---

## 6. Required states

Every view that loads data owes the user three states, and they are theme
components, not per-page improvisation. `08-gameday-states.html` shows all
three.

| State | Class | Rule |
|---|---|---|
| Loading | `.skeleton` inside the real layout | The skeleton mirrors the resolved layout so nothing jumps. Solid cloud at a pulsing opacity — **no shimmer sweep**, which is a gradient. |
| Empty | `.state` | Say what is missing *and* what will fill it. |
| Error | `.state.state--error` | Name the failure and the recovery. Pair with `.freshness.is-stale` in the header so a failed sync is visible where the fresh timestamp used to be. |

Stale data is never presented as current. `/api/health` already returns
`stale`; the header is where it shows.

---

## 7. Motion and focus

- Transitions use `--dur` (160ms) and `--ease`. Hover changes background only.
- `prefers-reduced-motion` zeroes `--dur` and stops the skeleton pulse.
- One focus ring for the whole theme: 2px navy, 2px offset. Navy is the only
  colour that works both on white panels and on the blue field.

---

## 8. Adding a page

1. Copy the shell from `07-gameday-standings.html`.
2. Load the four stylesheets in order. Write **no** page-local CSS.
3. Fill all three parts of `.field-head`; mark the nav item `aria-current`.
4. Put content in a `.panel` with an `.argyle-mark` + `.sec-head` head.
5. Pick components from the inventory below. If nothing fits, add the component
   to `gameday.css` with a comment saying what rule it follows.
6. Handle loading, empty, and error.
7. Run `./design/check-theme.sh`, then look at it at 390px as well as 1280px.

### Component inventory

| Need | Use |
|---|---|
| Page chrome | `.masthead` + `.masthead-utility` + `.masthead-section` |
| Page title block | `.field-head`, `.kicker`, `h1`, `.subline` |
| Container | `.panel`, `.panel-head`, `.panel-body`, `.panel-foot` |
| Section label | `.argyle-mark` + `.sec-head` (`h2`, `.spacer`, `.meta`) |
| People / teams as cards | `.grid` + `.card` + `.avatar` + `.team` + `.handle` |
| Columnar data | `.table-scroll` + `.stat-table` (`.rank`, `.num`, `.cell-team`) |
| Headline numbers | `.stat-strip` + `.stat` (`.figure`, `.label`) |
| Status word | `.tag`, `.tag--info`, `.tag--quiet` |
| Data age | `.freshness`, `.freshness.is-stale` |
| Action | `.btn` |
| Two-column page | `.wrap--wide` + `.layout` + `<aside class="rail">` + `<main>` |
| Rail card | `.rail-card`, `.rail-card--feature`, `.rail-head`, `.rail-list` |
| Rail navigation | `.rail-nav` + `.rail-link` (`[aria-current]`, `.is-soon`) |
| Divider | `.accent-rule` |
| Loading / empty / error | `.skeleton`, `.state`, `.state--error` |

---

## 9. Enforcement

`./design/check-theme.sh` is the mechanical half of this document — literal
hex, stock framework palettes, gradients, uniform radius, page-local `<style>`,
argyle stacking, and missing states. It exits non-zero, so it can be wired into
a hook or CI.

It cannot see the things that actually matter: whether the hierarchy reads,
whether the blue is doing work or just present, whether a real team name breaks
the layout at 390px. Check those by eye, with real league data, every time.
