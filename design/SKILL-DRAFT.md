---
name: unc-frontend
description: >
  Design rules for the fantasy dashboard's UNC Chapel Hill theme. Use whenever
  writing, editing, or reviewing anything the user sees — React components,
  CSS, Tailwind config, HTML mockups, icons, charts, or empty/error states.
  Triggers on "style", "theme", "layout", "component", "UI", "look", "design",
  "colour", "argyle", "make it look better", or any edit under frontend/.
---

# UNC frontend design

The brief is "clean and fun, and must not look vibe coded." Treat the second
half as the hard requirement — it is the one that gets failed silently.

**Read `design/GAMEDAY.md` first.** It is the theme's written rulebook —
colour, headers, fonts, layout, argyle, required states, and the recipe for a
new page. `design/gameday.css` is the same rules as code. What follows is the
short version plus the failure modes to watch for.

**Build pages from the theme layer, never beside it.** Load `tokens.css`,
`argyle.css`, `athletics.css`, `gameday.css` in that order and write no
page-local CSS. A page that needs its own styles has found a missing component:
add it to `gameday.css` with a comment saying which rule it follows.

## Non-negotiables

**Tokens only.** Every colour, size, space, and radius comes from
`design/tokens.css` as `var(--*)`. Never write a literal hex, rgb, or a raw
`px` font size in a component. If a value you need doesn't exist, add it to
the token file with a comment justifying it — don't inline it.

**Contrast is measured, not eyeballed.** Carolina Blue `#4B9CD3` is 3.00:1 on
white and 2.72:1 on Cloud. It is a *surface* colour on light backgrounds and a
*text* colour only on navy (4.84:1). Body text on light is Navy `#13294B` or
Bolin Creek `#2C5080`. UNC's own rule: Carolina Blue text must be ≥24px, or
19px bold. Never put Carolina Blue text on Cloud — it fails at every size.

**Brand balance.** Aim for roughly 40% Carolina Blue, 15% Navy, 25% White, 15%
secondary, 5% supporting. Hit the blue quota with fields and fills, not text.

**Argyle is a signature, not a texture.** The forms in `design/argyle.css` are
ranked by loudness — `.argyle-field`, `.argyle-stripe`, `.argyle-veil`,
`.argyle-mark`. A page uses at most one of the first three, plus the mark.
Diamonds are 3:4 (taller than wide). The overstitch lines must run parallel to
the diamond edges and pass through the centres, or it reads as a generic
diamond grid. Never place it behind body text.

**Type.** Two families, no third: **Oswald** for the display voice (wordmarks,
headings, kickers, buttons, team names, figures, table headers) and **Open
Sans** for anything read in sentences. Oswald is a voice, not a text face —
never set a paragraph or a table cell in it. Sizes come from the `--step-*`
scale only; tracking scales with size (0.18em kickers → 0.01em headings).

## The tells to avoid

These are what "vibe coded" actually looks like. Any one of them is a defect:

- Default framework palettes — `slate`, `gray`, `zinc`, `neutral`, `indigo`,
  `purple`. The untouched Tailwind grey ramp is the single clearest signature.
- Decorative gradients, especially blue→purple. Flat brand colour unless there
  is a stated functional reason.
- One border radius on everything. Radius varies by role: pill for status,
  `--radius-md` for panels, `--radius-sm` for tags.
- A drop shadow on every surface. One elevation level, used sparingly.
- Emoji standing in for icons.
- Proportional figures in numeric columns. Data always gets `tabular-nums`.
- Arbitrary sizes (`text-[13px]`, `mt-[7px]`) instead of the scale.
- Everything centred in one `max-w-4xl` column with no other structure.
- Uniform visual weight — nothing is clearly primary.
- Only the happy path designed. Loading, empty, error, and stale states are
  part of the work, not follow-ups.
- Placeholder content that flatters. Always test with real data, including the
  long team names and the members with no team name set.

## Before saying a frontend change is done

1. `./design/check-theme.sh` — must exit 0.
2. `npm --prefix frontend run build` — must pass.
3. **Look at it.** Start the app, screenshot with Playwright at 1280px and at
   390px, and actually examine the screenshot. Do not report a visual change as
   working without having viewed it.
4. Verify every interface state, not just the populated one: empty roster,
   failed sync (amber), long team name, missing team name. `08-gameday-states.html`
   shows what each is supposed to look like.

## Judgement

When a request is ambiguous, prefer restraint: fewer colours, fewer weights,
more whitespace, stronger hierarchy. "Fun" comes from a confident typographic
voice, real content, and one well-placed signature element — not from adding
decoration. If you're about to add a third accent colour or a second pattern,
stop and ask instead.
