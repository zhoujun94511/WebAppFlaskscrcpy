// Regression tests for B1 audit fixes (frontend).
//
// Each test pins a real bug found during the cross-service audit:
//
//   B1·#5  useStreamQuality must NOT burn its local cooldown on
//          dropped onTrigger calls. Originally lastFireTs was updated
//          unconditionally — if emitTo() silently failed (input
//          DataChannel not yet open) the monitor went silent for 4 s.
//
// B1·#3 (webrtc:closed → resetSession) and B1·#4 (detach monitor on
// scrcpy_status="connected") are harder to test in isolation because
// they require socket.io + the whole useScrcpySession wiring. They're
// covered by the inline JSDoc-style invariants in code; the
// integration-y validation belongs in an e2e harness, not vitest.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startStreamQuality } from "../src/composables/useStreamQuality.js";

// Reuse the same fake-clock / fake-stats helpers as the original
// useStreamQuality tests. Re-implemented here so this file stands
// alone if one is moved.

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
function setNow(ms) {
  fakeNowMs = ms;
}
function bumpNow(ms) {
  fakeNowMs += ms;
}

async function tick(advanceWallMs = 0) {
  bumpNow(advanceWallMs);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.resolve();
  await Promise.resolve();
}

describe("B1·#5 — useStreamQuality cooldown only after successful emit", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setNow(1_000_000);
    vi.spyOn(performance, "now").mockImplementation(() => fakeNowMs);
  });

  afterEach(() => {
    vi.useRealTimers();
    performance.now.mockRestore?.();
  });

  it("does NOT update lastFireTs when onTrigger returns false (drop)", async () => {
    // Simulate the failure mode: the DataChannel isn't open yet, so
    // emitTo() returns false. The monitor must remain free to retry
    // on the next poll — NOT burn 4 s of cooldown on a dropped trigger.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (reason) => {
        fired.push(reason);
        return false; // ← simulated drop
      },
    });
    await tick(0); // baseline
    // Drive a multi-tick freeze (1 s + 1 s = 2 s cumulative) to get
    // past the B5·R4 single-gap throttle clamp.
    await tick(1000);
    await tick(1000);
    expect(fired.length).toBe(1);

    // 1 s later — well inside the 4 s cooldown window. Pre-fix this
    // would have suppressed; post-fix it tries again.
    await tick(1000);
    mon.stop();
    expect(fired.length).toBeGreaterThanOrEqual(2);
  });

  it("DOES update lastFireTs when onTrigger returns true (success)", async () => {
    // Mirror image: a successful emit should still burn the cooldown
    // so the monitor doesn't spam reset requests every poll.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (reason) => {
        fired.push(reason);
        return true; // ← reached the wire
      },
    });
    await tick(0);
    await tick(1000);
    await tick(1000);
    expect(fired.length).toBe(1);
    // Inside the cooldown — must NOT fire.
    await tick(1500);
    mon.stop();
    expect(fired.length).toBe(1);
  });

  it("treats undefined return as success (back-compat)", async () => {
    // Callers that don't propagate emit success (older code paths or
    // tests that ignore the return) should keep the original
    // "fire & burn cooldown" semantics. Defensive contract.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: () => {
        fired.push("event");
        // implicit return undefined
      },
    });
    await tick(0);
    await tick(1000);
    await tick(1000);
    expect(fired.length).toBe(1);
    await tick(1500);
    mon.stop();
    expect(fired.length).toBe(1); // cooldown still applied
  });

  it("treats thrown errors as failed delivery (allows retry)", async () => {
    // If the callback explodes we don't have a positive delivery
    // signal — treat as failure so the next poll can try again. Same
    // reasoning as the explicit ``false`` branch.
    const fired = [];
    const src = makeStatsSource({ framesDecoded: 1000 });
    const mon = startStreamQuality({
      getPc: () => src.pc,
      isStreaming: () => true,
      onTrigger: (reason) => {
        fired.push(reason);
        throw new Error("transport choked");
      },
    });
    await tick(0);
    await tick(1000);
    await tick(1000);
    expect(fired.length).toBe(1);
    await tick(1000);
    mon.stop();
    expect(fired.length).toBeGreaterThanOrEqual(2);
  });
});
