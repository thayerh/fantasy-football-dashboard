"""Sleeper-only ADP, read from the FantasyPros real-time ADP page.

Why this needs a browser when the cheat sheet does not: the cheat-sheet page
ships its rankings as a JSON blob inside the HTML, so `httpx` is enough. This
page ships an empty table and fills it in from JavaScript, so fetching the HTML
returns nothing at all. Rendering it is the only way to see the numbers.

Why it is worth the trouble: the table has a **SLEEPER** column, and that is the
one number this league actually drafts against. FantasyPros' JSON API will not
give it to us — the free tier ignores the `filters` parameter that isolates a
single source, so it answers with a five-platform average instead.

Why we render the page rather than calling the endpoint it calls: the page
authenticates its own request with a key embedded in its JavaScript. That key is
FantasyPros', not ours, and using it would be borrowing their credential to get
past the limits on ours. Loading the page in a browser is what the page is
served for; reading the table it draws is not.

Being a good guest, same as the cheat-sheet scraper: one page load per daily
sync, no crawling, and a failure is loud rather than silent. Nothing about the
page's shape is a contract, so the column is found by its *header text* rather
than its position — FantasyPros can reorder the table without breaking this,
and if the column disappears entirely the sync fails instead of quietly
recording an empty set.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

BASE = "https://www.fantasypros.com/nfl/real-time-adp"

# Only the PPR page has been verified against real output; the others follow
# FantasyPros' usual slug pattern and are here so the scoring setting is not a
# lie. If one 404s the extractor will say so rather than guess.
ADP_SLUGS = {
    "PPR": "ppr",
    "HALF": "half-point-ppr",
    "STD": "standard",
}

# The column we are here for, and the header that identifies it.
SLEEPER_COLUMN = "SLEEPER"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Runs in the page. Returns entries already shaped like FantasyPros' JSON API
# rows, so ingest can reuse the same match ladder the other two syncs use.
_EXTRACT_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  let table = null;
  let head = [];
  for (const candidate of document.querySelectorAll('table')) {
    const headers = [...candidate.querySelectorAll('thead th')]
      .map((th) => norm(th.innerText).toUpperCase());
    if (headers.includes('SLEEPER') && headers.includes('NAME')) {
      table = candidate;
      head = headers;
      break;
    }
  }
  if (!table) {
    const seen = [...document.querySelectorAll('table')].map((t) =>
      [...t.querySelectorAll('thead th')].map((th) => norm(th.innerText)).join('|'));
    return { error: 'no table carries both a NAME and a SLEEPER column', headers: seen };
  }

  const iName = head.indexOf('NAME');
  const iSleeper = head.indexOf('SLEEPER');
  const iPos = head.findIndex((h) => h.startsWith('POS'));
  const iRank = head.indexOf('RK');

  const number = (text) => {
    const value = parseFloat(norm(text).replace(/[^0-9.]/g, ''));
    return Number.isFinite(value) ? value : null;
  };

  const rows = [];
  for (const tr of table.querySelectorAll('tbody tr')) {
    const cells = [...tr.querySelectorAll('td')];
    if (cells.length <= iSleeper) continue;  // ad rows and spacers

    const cell = cells[iName];
    const link = cell ? cell.querySelector('a') : null;
    const whole = norm(cell ? cell.innerText : '');
    const name = norm(link ? link.innerText : '') || whole.split('(')[0].trim();

    // Whatever follows the name carries the team and the bye week. Reading it
    // as a remainder rather than one strict regex keeps free agents and
    // players with no bye listed from dropping out of the set.
    const rest = whole.slice(name.length);
    const team = (rest.match(/\\b([A-Z]{2,3})\\b/) || [])[1] || null;
    const bye = (rest.match(/\\((\\d+)\\)/) || [])[1] || null;

    const posRank = iPos >= 0 ? norm(cells[iPos] ? cells[iPos].innerText : '') : '';

    rows.push({
      player_name: name,
      player_team_id: team,
      player_position_id: posRank.replace(/[0-9]+$/, '') || null,
      player_bye_week: bye,
      pos_rank: posRank || null,
      sleeper_adp: number(cells[iSleeper].innerText),
      overall_rank: iRank >= 0 ? number(cells[iRank].innerText) : null,
    });
  }
  return { rows, headers: head };
}
"""


class AdpScrapeError(RuntimeError):
    """The ADP page could not be rendered, or its Sleeper column was not found."""


def fetch_sleeper_adp(*, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Render the real-time ADP page and return its Sleeper column.

    One entry per player who has a Sleeper ADP; players the other platforms
    rank but Sleeper does not are dropped here rather than stored as nulls.
    """
    settings = settings or get_settings()
    scoring = (settings.fantasypros_scoring or "PPR").upper()
    slug = ADP_SLUGS.get(scoring)
    if slug is None:
        raise AdpScrapeError(
            f"No ADP page known for scoring {scoring!r}; expected one of {sorted(ADP_SLUGS)}."
        )

    url = f"{BASE}/{slug}"
    timeout_ms = int(settings.fantasypros_browser_timeout_seconds * 1000)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise AdpScrapeError(
            "playwright is not installed. Run `uv sync`, then `uv run playwright install "
            "chromium` if no system Chrome is available."
        ) from exc

    with sync_playwright() as pw:
        browser = _launch(pw, timeout_ms)
        try:
            page = browser.new_page(user_agent=_UA)
            page.set_default_timeout(timeout_ms)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # The table is drawn after the page's own data call returns, so
                # waiting for the row rather than for network idle: the page
                # keeps chattering to analytics long after the numbers land.
                page.wait_for_selector("table tbody tr", timeout=timeout_ms)
                result = page.evaluate(_EXTRACT_JS)
            except PlaywrightError as exc:
                raise AdpScrapeError(f"Could not render {url}: {exc}") from exc
        finally:
            browser.close()

    if result.get("error"):
        raise AdpScrapeError(
            f"{result['error']} at {url}. Tables seen: {result.get('headers')}"
        )

    rows = [row for row in result.get("rows") or [] if row.get("sleeper_adp") is not None]
    if not rows:
        raise AdpScrapeError(
            f"The {SLEEPER_COLUMN} column at {url} rendered empty. Headers: "
            f"{result.get('headers')}"
        )

    log.info("Scraped Sleeper ADP for %d players from %s", len(rows), url)
    return rows


def _launch(pw: Any, timeout_ms: int) -> Any:
    """Prefer the system Chrome, fall back to Playwright's own build.

    Using the installed browser means `playwright install` — a ~150 MB download
    — is not a deployment step on a desktop that already has Chrome.
    """
    from playwright.sync_api import Error as PlaywrightError

    try:
        return pw.chromium.launch(channel="chrome", headless=True, timeout=timeout_ms)
    except PlaywrightError as exc:
        log.debug("System Chrome unavailable (%s); using Playwright's chromium", exc)

    try:
        return pw.chromium.launch(headless=True, timeout=timeout_ms)
    except PlaywrightError as exc:
        raise AdpScrapeError(
            "No usable browser. Install Google Chrome, or run "
            "`uv run playwright install chromium`."
        ) from exc
