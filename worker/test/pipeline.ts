import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { Settings } from "../src/config.ts";
import type { LiveEvent } from "../src/feeds/base.ts";
import { evaluate } from "../src/analysis/pipeline.ts";

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

function primary(probHome: number): LiveEvent {
  return {
    eventId: "e1",
    sport: "nba",
    matchLabel: "A vs B",
    isLive: true,
    markets: [
      {
        marketType: "moneyline",
        line: null,
        outcomes: [
          { name: "home", decimalOdds: 1 / probHome, volume: 0 },
          { name: "away", decimalOdds: 1 / (1 - probHome), volume: 0 },
        ],
      },
    ],
    rawScore: "",
    sourceName: "espn",
  };
}

function corroborating(marketHome: number, volume: number): LiveEvent {
  return {
    eventId: "x",
    sport: "nba",
    matchLabel: "A vs B",
    isLive: true,
    markets: [
      {
        marketType: "moneyline",
        line: null,
        outcomes: [
          { name: "home", decimalOdds: marketHome, volume },
          { name: "away", decimalOdds: 3.0, volume },
        ],
      },
    ],
    rawScore: "",
    sourceName: "polymarket",
  };
}

describe("pipeline", () => {
  it("tiers big edges as obvious", () => {
    const sel = evaluate(primary(0.7), [corroborating(2.0, 500)], cfg);
    assert.ok(sel);
    assert.equal(sel.tier, "obvious");
    assert.ok(sel.thesis.length > 0 && sel.invalidation.length > 0);
  });

  it("discards single-source outliers", () => {
    assert.equal(evaluate(primary(0.7), [], cfg), null);
  });

  it("withholds thin-volume markets", () => {
    assert.equal(evaluate(primary(0.7), [corroborating(2.0, 1)], cfg), null);
  });

  it("discards markets below the value floor", () => {
    assert.equal(evaluate(primary(0.5), [corroborating(2.0, 500)], cfg), null);
  });

  it("ignores zero-volume exactly-2.00 placeholder prices", () => {
    const phantom = corroborating(2.0, 0);
    assert.equal(evaluate(primary(0.7), [phantom], cfg), null);
  });
});
