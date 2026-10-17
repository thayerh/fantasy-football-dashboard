# Appendix A — Sleeper Settings

Every rule in this constitution mapped to the actual Sleeper toggle that implements it. Use this to configure the league, and re-verify it every August.

## League Settings

| Sleeper setting | Value |
|---|---|
| Total teams | 12 |
| League type | **Dynasty** (Keeper: all) |
| Divisions | 0 |
| Scoring | Custom (see below) |
| Trade deadline | Week 12 |
| Trade review | **None / automatic** |
| Waiver type | **FAAB** |
| Waiver budget | 100 |
| Waiver clear days | 2 |
| Waiver process time | 3:00 AM ET, daily |
| Playoff teams | 6 |
| Playoff start week | 15 |
| Playoff round type | 1 week per round |
| Playoff seed type | Re-seed |
| Championship week | 17 |
| Consolation bracket | **On** — but see the toilet bowl note below |

## Roster Settings

| Slot | Count |
|---|---|
| QB | 1 |
| RB | 2 |
| WR | 3 |
| TE | 1 |
| FLEX (RB/WR/TE) | 2 |
| SUPER_FLEX (QB/RB/WR/TE) | 1 |
| BN (bench) | 13 |
| IR | 3 |
| Taxi | 3 |
| **Active roster size** | **23** |

No K and no D/ST slots. Set both to 0.

## Taxi Squad Settings

| Setting | Value |
|---|---|
| Taxi slots | 3 |
| Allow non-rookies for taxi | **Off** |
| Max years experience | 1 |
| Taxi deadline | **Week 2 for the inaugural season, Week 1 every year after** |

> **Sleeper quirk:** players cannot be added directly to a taxi slot. They must be claimed to the active roster first, then moved. Tell your league this before the rookie draft — it catches new dynasty owners every year.

> **Sleeper quirk:** Sleeper has no taxi-raiding feature and this league does not want one. Nothing to configure.

> **Change this after year one.** Sleeper's taxi deadline is a week number. Set it to **2** for the inaugural season and to **1** every season after ([Rosters §2.3](02-rosters-and-lineups.md#23-taxi-squad)). It is on the annual reset checklist below because it is the single easiest setting to leave stale.

## Startup Draft Settings

| Setting | Value |
|---|---|
| Draft type | Snake |
| Rounds | 20 |
| Pick timer | **90 seconds at start** — stepped to 35s before R4, 15s before R11 |
| Draft order | Manual, set by the commissioner |
| Trades during draft | **Disabled** |

**Stepping the clock.** Sleeper has one pick-timer value, but the commissioner can change it while the draft is live. Two changes only: before **round 4** drop to 35s, before **round 11** drop to 15s. See [Startup Draft §5.3](05-startup-draft.md#53-timing).

> **Do a dry run.** Open a throwaway mock draft and practice changing the timer mid-draft *before* draft night. Finding the setting for the first time with 12 people watching and a 90-second clock running is not the moment to learn the UI.

## Scoring Settings

Set scoring to **Custom** and enter these exactly:

| Stat | Sleeper key | Value |
|---|---|---|
| Passing yards | `pass_yd` | 0.04 |
| Passing TD | `pass_td` | 4 |
| Interception | `pass_int` | −2 |
| 2pt pass | `pass_2pt` | 2 |
| Rushing yards | `rush_yd` | 0.1 |
| Rushing TD | `rush_td` | 6 |
| 2pt rush | `rush_2pt` | 2 |
| Reception | `rec` | 1.0 |
| Reception (TE) | `bonus_rec_te` | 0 |
| Receiving yards | `rec_yd` | 0.1 |
| Receiving TD | `rec_td` | 6 |
| 2pt reception | `rec_2pt` | 2 |
| Fumble lost | `fum_lost` | −2 |
| Fumble recovery TD | `fum_rec_td` | 6 |
| Kick return TD | `kr_td` | 6 |
| Punt return TD | `pr_td` | 6 |

Set every bonus category (`bonus_rush_yd_100`, `bonus_pass_yd_300`, etc.) to **0**.

> Return touchdowns sit under special teams in Sleeper's custom scoring rather than with the offensive categories. Confirm the two return keys are set — they are the easiest rows on this table to scroll past, and they implement [§3.4](03-scoring.md#34-miscellaneous).

## Annual Reset Checklist

Sleeper does **not** do these automatically. The commissioner must do them by hand each year:

1. **Reset FAAB budgets to $100** for all 12 teams, after the rookie draft.
2. **Set the rookie draft order** — Sleeper does not know this league's ordering rules ([§6.2](06-rookie-draft.md#62-draft-order)): non-playoff teams in reverse standings, then playoff teams worst finish to champion.
3. **Create the rookie draft** with the correct type (linear), rounds (4), and a flat 90-second clock — and set the date agreed in the annual availability poll.
4. **Re-verify taxi eligibility settings** — experience thresholds roll over at season end and are easy to leave stale. **Before season two, change the taxi deadline from Week 2 to Week 1** and leave it there permanently.
5. **Confirm the trade deadline week** after Sleeper's season rollover.

## Known Divergences

Sleeper cannot enforce these rules. They are enforced by the commissioner:

- The **dues-paid requirement for trading future picks** ([§8.3](08-trades.md#83-trading-draft-picks)) — Sleeper has no concept of dues.
- The **lineup integrity / anti-tanking rules** ([§9.3](09-governance-and-conduct.md#93-tanking-and-lineup-integrity)) — Sleeper will happily let a team start six players on bye.
- The **48-hour window to move a cleared IR player** — Sleeper flags the illegal roster but does not act on it.
- **Playoff stat corrections** ([§3.6](03-scoring.md#36-stat-corrections)) — the commissioner must check manually before payouts.
- The **annual trade requirement for payout eligibility** ([§8.8](08-trades.md#88-the-annual-trade-requirement)) — Sleeper logs every trade but has no notion of a per-season minimum. Read the transaction log before paying anyone.
- The **taxi promotion deadline being one-way** — Sleeper enforces the direction correctly, but nothing stops an owner from promoting a taxi player they meant to keep stashed. That is on the owner.
- The **placement games** ([§4.2](04-schedule-and-playoffs.md#42-playoffs)) — the 5th place game in Week 16 and the 3rd place game in Week 17 decide picks 1.07–1.10. Confirm before Week 15 that eliminated playoff teams actually have Week 16 and Week 17 matchups; if Sleeper does not schedule them, pair the teams by hand and announce it in the group text.
- The **toilet bowl** ([§4.5](04-schedule-and-playoffs.md#45-the-toilet-bowl)) — Sleeper's consolation bracket advances winners; the toilet bowl advances losers. Leave the consolation bracket on so the six non-playoff teams still get scheduled matchups, then post and track the real bracket in the group text. The trophy goes to the team that loses the Week 17 final, regardless of what Sleeper's bracket displays.
