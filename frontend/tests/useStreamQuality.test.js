// Vitest unit tests for the front-end stream-quality monitor.
//
// Strategy
// --------
// The composable uses two independent clocks: ``setInterval`` for the
// 1 s polling cadence, and ``performance.now()`` for the freeze /
// cooldown deltas. We give each a separately-controlled fake so a
// scenario can advance one without the other — e.g. "two interval
// ticks happened but only 1.6 s of wall time elapsed" (real-world
// jitter where the freeze detector is supposed to fire).
//
// Pins the same edges the original stand-alone node script covered,
// but runs in ~30 ms total under fake timers instead of ~10 s.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startStreamQuality } from "../src/composables/useStreamQuality.js";

// ─── shared fake stats source ─────────────────────────────────────

function makeStatsSource(initial = {}) {
  const state = {
    framesDecoded: initial.framesDecoded ?? 0,
    framesDropped: initial.framesDropped ?? 0,
    freezeCount: initial.freezeCount ?? 0,
    totalFreezesDuration: initial.totalFreezesDuration ?? 0,
  };
  let getStatsCalls = 0;
  return {
    state,
    get getStatsCalls() {
      return getStatsCalls;
    },
    pc: {
      getStats() {
        getStatsCalls += 1;
        const m = new Map();
        m.set("inbound", {
          type: "inbound-rtp",
          kind: "video",
          ...state,
        });
        return Promise.resolve(m);
      },
    },
  };
}

// performance.now() is controlled separately from setInterval so a
// scenario can model "a tick fired AND meanwhile X ms of wall time
// passed" — that's what makes the freeze detector trigger in the real
// world (interval jitter, GC pauses).
let fakeNowMs = 0;
const originalPerformanceNow = performance.now.bind(performance);

function setNow(ms) {
  fakeNowMs = ms;
}

function bumpNow(ms) {
  fakeNowMs += ms;
}

// Fire one interval cycle synchronously. The composable's tick handler
// is async (it awaits pc.getStats()), so after ``advanceTimersByTime``
// we also flush pending microtasks with ``Promise.resolve``.
async function tick(advanceWallMs = 0) {
  bumpNow(advanceWallMs);
  await vi.advanceTimersByTimeAsync(1000);
  // Belt-and-braces — the async tick has two awaits; one round of
  // microtask flushing isn't always enough.
  await Promise.resolve();
  await Promise.resolve();
}

// ─── tests ────────────────────────────────────────────────────────

describe("useStreamQuality", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setNow(1_000_000); // start at a realistic-looking timestamp
    vi.spyOn(performance, "now").mockImplementation(() => fakeNowMs);
  });

  afterEach(() => {
    vi.useRealTimers();
    performance.now.mockRestore?.();
    // Defensive: restore the bound original in case mockRestore failed.
    if (performance.now !== originalPerformanceNow) {
      performance.now = originalPerformanceNow;
    }
  });

  it("does not fire when an encoder stall is shorter than the freeze threshold", async () => {
    // Baseline at t=1_000_000. Next tick at t=1_001_000 (wall +1000ms,
    // under the 1500ms threshold). Decoded held → no freeze fires.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(0); // baseline
    await tick(1000); // 1s wall → under threshold
    mon.stop();
    expect(fired).toEqual([]);
  });

  it("does NOT fire on a single huge gap (treated as tab-throttle, see B5·R4)", async () => {
    // Semantic change post-B5·R4: a single poll spanning > threshold
    // could be either a real freeze OR a browser tab-throttle gap
    // (background tab). We can't tell from the inside, so we forgive
    // the first big gap and only fire if the stall accumulates
    // across multiple polls. The "fires 'freeze' across MULTIPLE
    // steady ticks" test below covers the real-freeze path.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(0); // baseline
    await tick(2000); // 2s gap — looks like throttle, don't fire
    mon.stop();
    expect(fired).toEqual([]);
  });

  it("fires 'freeze' across MULTIPLE steady ticks once the cumulative stall exceeds the threshold", async () => {
    // Regression for a real production bug: the original implementation
    // measured ``now - prev.ts``, where ``prev`` was reassigned every
    // tick, so ``now - prev.ts`` was always ≈ one poll interval and
    // could never cross the 1.5s freeze threshold under steady 1-second
    // polling. The fix tracks ``stallStartTs`` separately so the
    // elapsed-stall window can accumulate across ticks. Without that
    // fix this test fails: two consecutive 1s ticks (each individually
    // under threshold) should still cumulatively fire freeze.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(0); // baseline at t=1_000_000
    await tick(1000); // t=1_001_000, decoded stuck (1000ms — under threshold)
    await tick(1000); // t=1_002_000, decoded still stuck (2000ms — over threshold)
    mon.stop();
    expect(fired).toContain("freeze");
  });

  it("resets the stall window once decoded frames start advancing again", async () => {
    // Counterpart to the regression above: after a partial stall, if
    // decoded frames resume advancing, stallStartTs must clear so the
    // *next* stall measures from its own starting point. Otherwise a
    // long-running session would accumulate "ghost stall" credit.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(0); // baseline
    await tick(1000); // 1s stall accumulating (under threshold)
    // Frames resume — this should clear stallStartTs entirely.
    src.state.framesDecoded = 1060;
    await tick(1000);
    // Decoded stops advancing again, but only for 1s — should NOT
    // fire because the window restarts after the recovery.
    await tick(1000);
    mon.stop();
    expect(fired).toEqual([]);
  });

  it("fires 'drops' when drop ratio exceeds 5% of decoded delta", async () => {
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000, framesDropped: 0 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(0); // baseline
    // 60 new frames, 6 dropped = 10% (over the 5% threshold)
    src.state.framesDecoded = 1060;
    src.state.framesDropped = 6;
    await tick(1000);
    mon.stop();
    expect(fired).toContain("drops");
  });

  it("fires 'browser-freeze' when the browser's freezeCount grows", async () => {
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000, freezeCount: 0 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(0);
    // Decoded advances normally so no freeze signal — but freezeCount
    // bumped which means the browser itself flagged a frozen frame.
    src.state.framesDecoded = 1060;
    src.state.freezeCount = 1;
    await tick(1000);
    mon.stop();
    expect(fired).toContain("browser-freeze");
  });

  it("suppresses repeat triggers inside the 4s local cooldown", async () => {
    // Drive a real (multi-poll) freeze to get past the B5·R4 throttle
    // clamp, then verify the local cooldown suppresses a second
    // trigger condition arriving inside the 4s window.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => {
        fired.push(r);
        return true; // emit succeeded → cooldown advances
      },
    });
    await tick(0); // baseline
    await tick(1000); // 1s stall — under threshold
    await tick(1000); // cumulative 2s — fires freeze
    expect(fired.length).toBe(1);
    // Another stalled poll 1s later — inside the 4s cooldown.
    await tick(1000);
    mon.stop();
    expect(fired.length).toBe(1);
  });

  it("never polls or fires while isStreaming() is false", async () => {
    const fired = [];
    let pcCalls = 0;
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => {
        pcCalls += 1;
        return src.pc;
      },
      isStreaming: () => false,
      onTrigger: (r) => fired.push(r),
    });
    await tick(1000);
    await tick(1000);
    mon.stop();
    expect(fired).toEqual([]);
    expect(pcCalls).toBe(0);
  });

  it("does not crash when getPc() returns null", async () => {
    const fired = [];
    const mon = startStreamQuality({
      getPc: () => null,
      isStreaming: () => true,
      onTrigger: (r) => fired.push(r),
    });
    await tick(1000);
    await tick(1000);
    mon.stop();
    expect(fired).toEqual([]);
  });

  it("stop() halts polling immediately", async () => {
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: () => {},
    });
    await tick(0);
    const observedAtStop = src.getStatsCalls;
    mon.stop();
    await tick(1000);
    await tick(1000);
    expect(src.getStatsCalls).toBe(observedAtStop);
  });
});
