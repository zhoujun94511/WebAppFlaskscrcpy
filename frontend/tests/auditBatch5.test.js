// Regression tests for B5 audit fixes (frontend).
//
// B5·R4  useStreamQuality must clamp stallStartTs against gap-on-resume
//        (browser tab throttle) so the first poll after a long
//        background pause doesn't fire a spurious freeze.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startStreamQuality } from "../src/composables/useStreamQuality.js";

function makeStatsSource(initial = {}) {
  const state = {
    framesDecoded: initial.framesDecoded ?? 0,
    framesDropped: initial.framesDropped ?? 0,
    freezeCount: initial.freezeCount ?? 0,
    totalFreezesDuration: initial.totalFreezesDuration ?? 0,
  };
  return {
    state,
    pc: {
      getStats() {
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

let fakeNowMs = 0;
function bumpNow(ms) {
  fakeNowMs += ms;
}

async function tick(advanceWallMs = 0) {
  bumpNow(advanceWallMs);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.resolve();
  await Promise.resolve();
}

describe("B5·R4 — useStreamQuality clamps stallStartTs against tab-throttle gaps", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fakeNowMs = 1_000_000;
    vi.spyOn(performance, "now").mockImplementation(() => fakeNowMs);
  });

  afterEach(() => {
    vi.useRealTimers();
    performance.now.mockRestore?.();
  });

  it("does not fire a spurious freeze after a long background-throttle gap", async () => {
    // Sequence:
    //   t=0    first poll baseline (decoded=1000)
    //   tab goes to background → setInterval throttled
    //   t=30s  first foreground poll, decoded still 1000 (real frames
    //          stalled while backgrounded, but we don't know if the
    //          REAL stall was 30s or just 1s of decoded-stuck after a
    //          29s of throttle gap with no info).
    //
    // Pre-fix: stallStartTs = prev.ts (= t=0) → 30s elapsed →
    //          instantly fires "freeze".
    // Post-fix: stallStartTs = max(prev.ts, now - 1s) = t=29s →
    //          only 1s elapsed → under 1.5s threshold, no fire.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => {
        fired.push(r);
        return true;
      },
    });
    await tick(0); // baseline
    // Simulate ~30 s of tab-throttle: now jumps but ONE poll fires
    // when the tab returns to foreground. decoded didn't advance.
    await tick(30_000);
    mon.stop();
    expect(fired).toEqual([]);
  });

  it("still fires when stall accumulates across MULTIPLE real polls", async () => {
    // Sanity counterpart: the clamp shouldn't blind us to actual
    // sustained stalls. Two consecutive 1 s polls with stuck decoded
    // = 2 s of real stall, must fire (matches the existing B1 spec).
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => {
        fired.push(r);
        return true;
      },
    });
    await tick(0); // baseline
    await tick(1000); // 1 s, decoded stuck — under threshold
    await tick(1000); // another 1 s, cumulative 2 s — over threshold
    mon.stop();
    expect(fired).toContain("freeze");
  });

  it("recovers normal detection after the throttle gap", async () => {
    // After the throttle-induced suppressed-fire, if the stream is
    // ACTUALLY still stalled, subsequent polls accumulate the real
    // stall window and fire. The clamp only forgives the FIRST gap.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (r) => {
        fired.push(r);
        return true;
      },
    });
    await tick(0); // baseline
    await tick(30_000); // throttle gap — no fire (clamped)
    await tick(1000); // real stall, decoded still 1000 → accumulated 2s
    mon.stop();
    expect(fired).toContain("freeze");
  });
});
