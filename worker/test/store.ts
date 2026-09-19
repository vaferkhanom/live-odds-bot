import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Store } from "../src/store.ts";

/**
 * Minimal D1-compatible shim over node:sqlite that executes the REAL
 * migration SQL, so store queries are validated against SQLite semantics.
 */
function makeD1() {
  const db = new DatabaseSync(":memory:");
  const migDir = join(dirname(fileURLToPath(import.meta.url)), "..", "migrations");
  const schema = readFileSync(join(migDir, "0001_init.sql"), "utf8");
  db.exec(schema);
  try {
    db.exec(readFileSync(join(migDir, "0002_outcome_label.sql"), "utf8"));
  } catch {
    /* already applied */
  }
  db.exec(schema);
  const exec = (stmt: any, params: unknown[]) => ({
    first: <T>() => (stmt.get(...params) ?? null) as T | null,
    all: <T>() => ({ results: (stmt.all(...params) ?? []) as T[] }),
    run: () => {
      stmt.run(...params);
      return {};
    },
  });
  const wrap = (stmt: any) => ({
    bind: (...params: unknown[]) => exec(stmt, params),
    ...exec(stmt, []),
  });
  return { prepare: (sql: string) => wrap(db.prepare(sql)) } as unknown as D1Database;
}

function selection(over: Record<string, unknown> = {}) {
  return {
    sport: "nba",
    match_label: "A vs B",
    event_id: "e1",
    market_type: "moneyline",
    line: null,
    outcome: "home",
    outcome_label: "Win for A",
    odds_decimal: 2.0,
    tier: "obvious",
    edge: 0.07,
    ev: 0.1,
    stake_cap: 2.0,
    thesis: "t",
    invalidation: "i",
    sources: ["espn", "polymarket"],
    ...over,
  };
}

describe("store (D1, real SQL)", () => {
  let store: Store;
  before(() => {
    store = new Store(makeD1());
  });

  it("saves, dedupes open picks, and allows re-entry after settlement", async () => {
    const first = await store.saveSelection(selection());
    assert.ok(first);
    assert.equal(await store.saveSelection(selection()), null);
    await store.settle(first!, "won");
    const reopened = await store.saveSelection(selection());
    assert.ok(reopened && reopened !== first);
    assert.equal(await store.saveSelection(selection()), null);
  });

  it("manages subscriptions and alert records", async () => {
    const sub = await store.ensureSubscription("123");
    assert.equal(sub.chat_id, "123");
    await store.setSubscription("123", { subscribed: 1, tiers: "obvious,value" });
    const tiered = await store.subscribersForTier("obvious");
    assert.ok(tiered.some((s) => s.chat_id === "123"));
    const open = await store.getOpen();
    assert.ok(open.length >= 1);
    await store.recordAlert(open[0]!.id, "123", "sent");
    assert.equal(await store.alertExists(open[0]!.id, "123"), true);
  });

  it("tracks gaps and daily stats", async () => {
    await store.upsertGap("nba", "cards-total", "no-reliable-feed");
    await store.upsertGap("nba", "cards-total", "no-reliable-feed");
    const gaps = await store.getGaps();
    assert.equal(gaps.filter((g) => g.market_type === "cards-total").length, 1);
    const report = await store.statsForDay();
    assert.ok(report.picks.length >= 1);
    assert.ok((report.counts["won"] ?? 0) >= 1);
    const record = await store.recordAlltime();
    assert.ok(record.some((r) => r.status === "won"));
  });
});
