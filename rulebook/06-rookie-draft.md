# 06 — Rookie Draft

Every spring, after the NFL Draft. This is the league's annual heartbeat and the mechanism by which bad teams get better.

## 6.1 Format

| Setting | Value |
|---|---|
| Rounds | **4** |
| Order type | **Linear** (same order every round — *not* a snake) |
| Player pool | Current-year NFL rookies only |
| Format | **Live draft, one sitting** |
| Timing | A date set each year by the commissioner, within **4 weeks of the NFL Draft** |
| Pick clock | **90 seconds**, flat — no tiers |
| Length | 48 picks · **72 minutes** worst case, realistically 40–50 |
| Exception | A [distressed-orphan compensatory pick](09-governance-and-conduct.md#95-vacant-teams-orphans) adds a 49th pick at the end of round 2 in the year it is awarded |
| Autopick | Yes, best available by Sleeper ranking |

**Why linear and not snake.** In a rookie draft the order already reflects last season's standings; snaking it hands the best team the 1.12 *and* the 2.01 back-to-back and blunts the whole point. Linear is the dynasty standard.

**Why a flat clock.** The startup needs a [tiered clock](05-startup-draft.md#53-timing) because it is 240 picks. This draft is 48. At 90 seconds a pick the whole thing fits in 72 minutes even if every owner burns every second, so there is nothing to optimize and no reason to make the commissioner fiddle with settings mid-draft. One value, set once.

**Why 4 rounds.** Superflex leagues start ten players and rookie classes reliably produce ~48 worth stashing. Note the churn this creates against a 23-man roster: four incoming rookies a year means clearing four spots a year, roughly a third of your thirteen-man bench. The taxi squad absorbs three of them, which is precisely what it is for. Three rounds is the other common answer and would be the move if the annual cutdown starts feeling forced.

**Roster space — you do not need any.** Draft all of your picks regardless of how full your roster is. Sleeper permits drafting over the roster limit, so nobody has to cut four useful players in May to make room for rookies who are headed to the taxi squad anyway.

After the draft, move your rookies to taxi and drop whoever you were going to drop. Moving players to taxi and IR is permitted *while* you are over the limit, so the sequence works in the order you'd want it to.

**The one catch:** while a roster is over the limit, Sleeper locks your starting lineup and blocks free agent claims. That costs you nothing in May, but it means you must be back under the limit before your first matchup. The hard deadline is **Week 1 kickoff**, the same as every other roster compliance check ([Rosters §2.2](02-rosters-and-lineups.md#22-roster-limits)).

**Undrafted rookies** become free agents when the rookie draft concludes and are subject to the normal waiver process.

### Scheduling the draft

The date is **not fixed in this constitution** and changes every year. It is scheduled around the league's actual availability, because a live draft that half the league misses is worse than no live draft at all.

The commissioner runs this process:

1. **Within 3 days of the NFL Draft ending**, the commissioner posts a poll in the league chat with at least four candidate dates and times.
2. **Owners respond within 5 days.** No response is treated as "available for anything" — if you do not vote, you do not get to complain about the date.
3. **The commissioner announces the date**, choosing whichever slot the most owners can make. Ties go to the commissioner's judgment. The announcement comes with **at least 10 days' notice**.
4. **The date is locked once announced.** It moves only for a genuine leaguewide problem, never for one owner's schedule.

**The outer boundary:** the draft must be held **no later than 4 weeks after the NFL Draft concludes**. Rookie draft season is the best part of the dynasty offseason and letting it slide into July drains the momentum out of it. Inside that window, any date is fair game.

**If you cannot make it.** Queue your players and let autopick draft for you — with 4 rounds and a good queue this produces a fine result. Alternatively, send the commissioner a written ranked list before the draft and the commissioner will pick from it on your behalf.

Owners may **not** hand their draft to another owner. Autopick or a written list to the commissioner are the only two options, because an owner drafting for someone else is a collusion question nobody needs.

## 6.2 Draft Order

The six teams that **missed the playoffs** pick first (slots 1.01–1.06), followed by the six **playoff teams** (slots 1.07–1.12).

**Playoff teams (picks 7–12)** are ordered by how deep they went, and within each pair by the result of a [placement game](04-schedule-and-playoffs.md#42-playoffs):

| Pick | Team | Final place |
|---|---|---|
| 1.07 | **Winner** of the 5th place game (Week 16) | 5th |
| 1.08 | **Loser** of the 5th place game | 6th |
| 1.09 | **Winner** of the 3rd place game (Week 17) | 3rd |
| 1.10 | **Loser** of the 3rd place game | 4th |
| 1.11 | Runner-up | 2nd |
| 1.12 | Champion | 1st |

**The winner of each placement game takes the better pick.** How far you advanced sets your band — wild card losers pick at 1.07–1.08, semifinal losers at 1.09–1.10, finalists at 1.11–1.12 — and inside that band, winning the placement game moves you up a slot rather than down. That inversion is on purpose: an eliminated team should never be in a game it profits from losing.

**Honest caveat.** Advancing a round still costs you two draft slots — lose the wild card game and you are in the 1.07–1.08 band, win it and the best you can do is 1.09. That is true in every league that orders picks by playoff finish, and the counterweight is $180 for the championship and $75 for the runner-up ([Structure §1.5](01-league-structure.md#15-payouts)) against two slots at the back of round 1. Deliberately losing a playoff game to improve a pick is a [§9.3](09-governance-and-conduct.md#93-tanking-and-lineup-integrity) violation, and the penalty for a pattern of it is forfeiting a first-round pick — strictly worse than anything the tank would earn.

**Non-playoff teams (picks 1–6)** are ordered by **reverse order of the regular-season standings.** The worst record picks 1.01, the sixth-worst picks 1.06. Ties are broken by fewest points scored.

This is the system the NFL uses and the one every owner understands without explanation. Its known weakness is that it rewards losing: once a team is mathematically eliminated, finishing 12th is strictly better than finishing 7th. The league accepts that trade in exchange for a draft order nobody has to argue about, and leans on [§9.3](09-governance-and-conduct.md#93-tanking-and-lineup-integrity) — the requirement to field a complete, healthy lineup every week, enforced by the commissioner on their own initiative — to keep it honest.

If tanking becomes a visible problem, a weighted lottery among the six non-playoff teams is the standard remedy and can be adopted through the [normal amendment process](09-governance-and-conduct.md#91-amendments). Adopt it in an offseason where nobody has yet benefited from it.

**When the order is set.** Immediately after the championship game, and posted in the league group text.

## 6.3 Trading Rookie Picks

Rookie picks are tradeable — see [Trades §8.3](08-trades.md#83-trading-draft-picks) for the full rules, including the requirement that **dues be paid before trading a future pick**.

Picks in the current year's rookie draft may be traded up until the moment they are on the clock — including during the draft itself, which is half the fun of a live rookie draft.

Because the draft is live, trade pauses are tightly capped: the commissioner may pause the clock for up to **5 minutes**, no more than **3 times** in the draft. A trade that cannot be agreed and entered into Sleeper inside 5 minutes waits until the draft is over.

## 6.4 No Supplemental or Free-Agent Rounds

There is no veteran/free-agent draft. All non-rookie player acquisition happens through [waivers and free agency](07-waivers-and-free-agency.md) year-round.
