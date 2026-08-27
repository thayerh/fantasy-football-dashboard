# UNC theme

**The chosen theme is Gameday (direction 4), and its rules are in
[`GAMEDAY.md`](GAMEDAY.md).** That file plus `gameday.css` are what a new page
needs; everything else here is the exploration those rules came out of.

```
tokens.css     palette, type scale, spacing, radius, elevation
argyle.css     the four argyle forms, ranked by loudness
athletics.css  masthead, section heads, buttons, freshness pill
gameday.css    page shell and content components   ← the theme layer
GAMEDAY.md     the written rules
check-theme.sh mechanical enforcement of both
```

Three pages are built from that layer with **no page-local CSS at all**, which
is the standing proof that the theme repeats across content shapes:
`04-gameday.html` (card grid), `07-gameday-standings.html` (dense numeric
table), `08-gameday-states.html` (loading, empty, error).

Open `index.html` to see them, and the six original directions below them.

```bash
cd design && python3 -m http.server 8123
# then open http://127.0.0.1:8123
```

## The research that shaped these

Colours come from the official UNC values at
[identity.unc.edu/brand/color](https://identity.unc.edu/brand/color), with one
deliberate departure: **Carolina Blue is `#7BAFD4`**, the lighter older value
that circulates on colour-code aggregator sites, rather than the current
official `#4B9CD3` (PMS 542 C). Both were built and compared on the real page;
the lighter one was chosen on how it looks. The measured cost is below.

Two facts from the brand guidelines did most of the work:

**Carolina Blue must dominate.** The published balance is roughly 40% Carolina
Blue, 15% Navy, 25% White, 15% secondary, 5% supporting. A design that uses the
blue as a timid accent isn't on-brand, however tasteful it looks.

**Carolina Blue barely passes contrast.** UNC's own guidance requires Carolina
Blue text to be 24px, or 19px bold. Measuring it explains why:

| Foreground | On | `#7BAFD4` | (was `#4B9CD3`) | Body (4.5) | Large (3.0) |
|---|---|---:|---:|---|---|
| Carolina Blue | White | 2.35 | 3.00 | fail | **fail** |
| Carolina Blue | Cloud | 2.14 | 2.72 | fail | **fail** |
| Carolina Blue | Navy | 6.17 | 4.84 | pass | pass |
| Navy | Carolina Blue | 6.17 | 4.84 | pass | pass |
| White | Carolina Blue | 2.35 | 3.00 | fail | **fail** |
| Navy | White | 14.52 | — | pass | pass |
| Bolin Creek | White | 8.18 | — | pass | pass |
| Old Well Basin | White | 5.87 | — | pass | pass |
| Flat Gray | White | 2.36 | — | fail | fail |

The lighter blue is a clean trade: everything **on navy** improves by ~1.3
points, everything **white-on-blue and blue-on-white** loses ~0.65. That makes
the surface-not-text rule absolute — this blue no longer clears even the 3:1
non-text threshold on white — and it leaves the white display type on the blue
field as a knowing exception, documented in `GAMEDAY.md`.

Put together, these two facts are in tension, and resolving that tension is the
actual design problem:

> On light backgrounds, Carolina Blue is a **surface** colour, not a text
> colour. Reaching 40% blue means large fields, headers, and fills — not blue
> body copy. On navy, Carolina Blue flips and becomes a legitimate text colour.

Note in particular that Carolina Blue on Cloud (`#EDF5FB`) fails *all* text
sizes. Pale-blue-on-pale-blue is the most tempting mistake in this palette.

## Combinations (4–6) — the goheels chrome

Built after you picked Old Well and Franklin Street and supplied
`references/goheels.png`. All three share a chrome lifted from that page:

- a **two-tier masthead** — Carolina Blue utility bar above a Navy section bar
- the **chevron plate**, its slanted edge pointing into the navigation
- **heavy condensed caps** (Oswald) for wordmarks and section labels
- the **argyle diamond cluster** as a section-header ornament

That last one is the most valuable thing the reference gave us. Using argyle as
a small ornament before a label is far more disciplined than using it as a
background: unmistakably Carolina at a glance, zero cost to legibility, and it
survives on a dense page. It is now the default argyle treatment — `.argyle-mark`
in `argyle.css`.

One contrast note the reference happens to get right: its utility-bar links are
**navy on Carolina Blue** (4.84:1), not white on Carolina Blue (2.99:1, which
fails). The chrome here does the same.

### 4 · Gameday — goheels × Franklin Street — **chosen**
Masthead over the bright blue field and white card panels. The most energetic;
reads like a team app. Now codified in [`GAMEDAY.md`](GAMEDAY.md).

### 5 · Kenan — goheels × Old Well
Masthead over the ruled editorial list. The chrome carries the Carolina
identity, which frees the content area to stay quiet and dense.

### 6 · Sideline — all three
Follows the reference's actual page architecture: a narrow summary rail beside
a wide primary column. That structure is why goheels can carry a lot without
feeling like a dump, and it is the one that scales as standings, matchups and
transactions arrive — each becomes a rail card or a primary section rather than
another block stacked down a single centred column.

## The three original directions

### 1 · Old Well — editorial collegiate
Print-derived. Rules, a strong type hierarchy, and generous whitespace instead
of cards and shadows. The team name is set larger than the manager's handle,
because the team name is what people actually talk about. Argyle appears once,
as a masthead ribbon.

**Pick this if** you want something that still looks good in three years and
never reads as a template.

### 2 · Box Score — broadcast stat sheet
Navy field, tabular monospace figures, dense rows, the argyle used as the
jersey side-stripe it originally was. This is the only direction where Carolina
Blue does real work as *text*, because navy is the only background where it
passes.

**Pick this if** the dashboard is going to become standings, scores, and power
rankings — it is built for columns of numbers.

### 3 · Franklin Street — bright and social
Carolina Blue as the page field with white panels floating on it, roster as
cards, a Ginkgo-yellow commissioner tag. Hits the 40% blue mandate honestly
because a background carries no text-contrast burden.

**Pick this if** "clean and fun" is the priority and the league is the point,
rather than the statistics.

## Argyle rules

Alexander Julian designed the linked-diamond motif for Dean Smith's 1991–92
basketball uniforms. Getting it right is mostly about three things:

1. **Diamonds are taller than wide.** The tiles here are 3:4. Equilateral
   diamonds give you a harlequin print, not argyle.
2. **The overstitch is mandatory.** Thin diagonal lines must run *parallel to
   the diamond edges* and pass through the diamond centres. Without them the
   pattern reads as a generic diamond grid; at a wrong angle it reads as noise.
   This was visibly wrong on the first attempt and had to be rebuilt.
3. **Use it once per screen.** Argyle is a signature, not a texture. A ribbon,
   a stripe, or a watermark — never two at the same time, and never behind
   text you expect people to read.

`argyle.css` ranks its forms by loudness — `.argyle-field` (lattice),
`.argyle-stripe` (uniform side panel), `.argyle-veil` (white linework on the
blue field, Gameday's standard), `.argyle-mark` (the diamond cluster before a
section label). A page uses at most one of the first three, plus the mark.
Thin bands also need `.argyle-field--band`, which scales the tile down so a
ribbon shows whole diamonds rather than their tips.

## Typography

Both faces are free and brand-faithful rather than arbitrary:

- **Libre Franklin** for display — an open-source revival of ITC Franklin
  Gothic, which is UNC's actual display face.
- **Open Sans** for body — literally the brand's body face, free on Google Fonts.
- **IBM Plex Mono** for figures in the Box Score direction, so numeric columns
  align.

If we ship one of these, the fonts should be self-hosted rather than loaded
from Google's CDN — one less third-party dependency, and it keeps working if
you ever put the app behind a strict CSP.

## Wired into the app

The port is done. `frontend/src/index.css` imports these four stylesheets
directly, so the app and the mockups share one definition of the theme;
Tailwind is gone from the build entirely, and the fonts are self-hosted in
`frontend/src/fonts/`. `check-theme.sh` passes on both sections.

The homepage (`frontend/src/App.tsx`) is a rail page: `Rail` on the left with
placeholder categories, `StandingsPanel` as the primary column.

## Next step

Standings have no numbers yet. Wins, losses and points live on Sleeper's
`/league/{id}/rosters` endpoint, which nothing ingests — so the table renders
its columns empty rather than inventing zeroes. A `sync_rosters()` job plus a
`league_roster` table would fill them, and the same job pointed at
`previous_league_id` would bring in last season's final standings.
