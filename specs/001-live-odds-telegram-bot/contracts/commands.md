# Contracts: Live Odds Telegram Bot

## Bot commands

| Command | Args | Response |
|---|---|---|
| `/start` | — | Welcome + subscription status + `/help` pointer |
| `/help` | — | Full command list with one-line descriptions |
| `/opportunities` | — | All `open` selections: tier badge, match, market, odds, thesis summary |
| `/stats` | `[YYYY-MM-DD]` | Tehran-day table: every selection with ✅ won / ❌ lost / ➖ void / ⏳ pending + tally; defaults to today |
| `/record` | — | All-time tally by tier: counts + win rate (settled only) |
| `/subscribe` | `[obvious\|value\|all]` | Enables push alerts (default all) |
| `/unsubscribe` | — | Disables push alerts; pull commands still work |
| `/settings` | — | Shows tiers, quiet hours; sub-actions to change them |
| `/status` | — | Live snapshot: open picks right now + today's settled won/lost tally |
| `/api` | `set <key> \| status \| clear` | Priority API key: validated on set (key masked everywhere, set-message deleted); exhaustion triggers a one-time push alert and fallback to free feeds |
| `/coverage` | — | Monitored market catalog per sport + declared gaps |

Unauthorized chats receive a refusal message for every command.

## Alert message format

```text
🟢 OBVIOUS EDGE — Soccer · EPL
Arsenal vs Chelsea — Over 2.5 @ 2.10
Edge 8.2% · EV +0.17u · Cap 2u
Thesis: <one-line justification>
Invalid if: <condition>
```

Value tier uses 🟡 and identical fields. No alert may omit thesis or
invalidation.

## Settlement push format

Every newly settled pick is pushed live to its tier subscribers:

```text
✅ Bet WON 🟢
Arsenal vs Chelsea — Over 2.5: over @ 2.10
```

Variants: `❌ Bet LOST`, `➖ Bet VOID`. Same match/market/odds fields.

## Feed interface (internal)

Each price source implements: `fetch_live(sport) -> events[]`,
`fetch_final(event_id) -> result`. Events normalize to
`{event_id, match_label, starts_at, markets[{type, line, outcomes[{name,
decimal_odds}]}]}`. Adding a source MUST NOT change the analysis interface.
