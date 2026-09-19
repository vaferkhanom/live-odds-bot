import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { Settings } from "../src/config.ts";
import type { LiveEvent } from "../src/feeds/base.ts";
import { compatible, evaluateAll, outcomeLabel } from "../src/analysis/pipeline.ts";
import { isStale, matchFinal } from "../src/monitor.ts";

const cfg: Settings = {
  botToken: "",
  authorizedChatIds: [],
  obviousEdge: 0.06,
  valueEdge: 0.025,
  stakeCapObvious: 2.0,
  stakeCapValue: 1.0,
  minVolume: 100,
  fetchTimeoutMs: 10000,
};

function ev(label: string, markets: LiveEvent["markets"], source = "espn"): LiveEvent {
  return { eventId: "e1", sport: "soccer-epl", matchLabel: label, isLive: true, markets, rawScore: "", sourceName: source };
}

describe("breadth", () => {
  it("labels winners accurately", () => {
    assert.equal(outcomeLabel("1x2", "home", "Brentford vs Chelsea"), "Win for Brentford");
    assert.equal(outcomeLabel("moneyline", "away", "Brentford vs Chelsea"), "Win for Chelsea");
    assert.equal(outcomeLabel("1x2", "draw", "Brentford vs Chelsea"), "Draw");
    assert.equal(outcomeLabel("total", "over", "Brentford vs Chelsea"), "over");
  });

  it("evaluates every market with matching lines only", () => {
    const e = ev("A vs B", [
      { marketType: "moneyline", line: null, outcomes: [{ name: "home", decimalOdds: 1.7, volume: 0 }, { name: "away", decimalOdds: 2.4, volume: 0 }] },
      { marketType: "total", line: "2.5", outcomes: [{ name: "over", decimalOdds: 1.9, volume: 0 }, { name: "under", decimalOdds: 2.0, volume: 0 }] },
    ]);
    const cor = ev("A vs B", [
      { marketType: "moneyline", line: null, outcomes: [{ name: "home", decimalOdds: 2.6, volume: 500 }, { name: "away", decimalOdds: 1.6, volume: 500 }] },
      { marketType: "total", line: "3.5", outcomes: [{ name: "over", decimalOdds: 2.6, volume: 500 }, { name: "under", decimalOdds: 1.6, volume: 500 }] },
    ], "polymarket");
    const sels = evaluateAll(e, [cor], cfg);
    const types = new Set(sels.map((s) => s.market_type));
    assert.ok(types.has("moneyline"));
    assert.ok(!types.has("total")); // 3.5 line does not corroborate 2.5
    assert.ok(sels.every((s) => typeof s.outcome_label === "string"));
  });

  it("checks market compatibility", () => {
    assert.equal(compatible("total", "2.5", "total", "2.5"), true);
    assert.equal(compatible("total", "2.5", "total", "3.5"), false);
    assert.equal(compatible("moneyline", null, "1x2", null), true);
    assert.equal(compatible("moneyline", null, "total", null), false);
  });

  it("rejects stale fixtures", () => {
    const old = new Date(Date.now() - 30 * 3_600_000).toISOString();
    assert.equal(isStale({ eventId: "e", sport: "x", matchLabel: "A vs B", isLive: false, markets: [], rawScore: "", startsAt: old }), true);
    assert.equal(isStale({ eventId: "e", sport: "x", matchLabel: "A vs B", isLive: true, markets: [], rawScore: "", startsAt: old }), false);
    assert.equal(isStale({ eventId: "e", sport: "x", matchLabel: "A vs B", isLive: false, markets: [], rawScore: "" }), false);
  });

  it("fuzzy-matches finals by label", () => {
    const finals = { "101": { home: 2, away: 1, label: "Arsenal vs Chelsea" } };
    assert.deepEqual(matchFinal("Arsenal FC vs. Chelsea FC", finals), { home: 2, away: 1 });
    assert.equal(matchFinal("Lakers vs Celtics", finals), null);
  });
});
