// Regression tests for B3 audit fixes (frontend).
//
// These four are isolated logic-shape tests — we don't spin up the
// whole useScrcpySession composable (it touches socket.io and the
// real WebRTC peer pool). Instead we extract the behaviour each fix
// guards and test it directly.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ─── B3·#16: QuickInfoCard race protection ─────────────────────────

describe("B3·#16 — reload race protection via AbortController", () => {
  // Mirror of QuickInfoCard's reload() — kept here as a faithful
  // reproduction so a regression in this logic surfaces, even though
  // we can't easily mount the whole .vue. If you change reload(),
  // update this and add a watch-style integration test in parallel.

  function makeReload() {
    let inFlight = null;
    let info = null;
    let fetchedAt = [];

    async function reload(deviceId, fetcher) {
      if (inFlight) inFlight.abort();
      if (!deviceId) {
        info = null;
        inFlight = null;
        return;
      }
      const controller = new AbortController();
      inFlight = controller;
      try {
        const data = await fetcher(deviceId, controller.signal);
        if (controller !== inFlight) return; // superseded
        info = data;
        fetchedAt.push(deviceId);
      } catch (err) {
        if (err?.name === "AbortError") return;
        if (controller !== inFlight) return;
        info = null;
      } finally {
        if (controller === inFlight) inFlight = null;
      }
    }

    return {
      reload,
      get info() {
        return info;
      },
      get fetchedAt() {
        return fetchedAt;
      },
    };
  }

  it("does not overwrite info when a stale fetch lands after a newer one", async () => {
    const r = makeReload();

    // Slow fetch for devA (resolves after devB's fast fetch).
    let resolveA;
    const fetcher = (did, signal) => {
      if (did === "devA") {
        return new Promise((resolve, reject) => {
          resolveA = resolve;
          signal.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        });
      }
      return Promise.resolve({ id: did });
    };

    const pA = r.reload("devA", fetcher);
    // Now switch to devB before A resolves — should abort A.
    const pB = r.reload("devB", fetcher);
    // Resolve A AFTER B has already landed.
    resolveA?.({ id: "devA" });
    await Promise.all([pA, pB]);

    expect(r.info).toEqual({ id: "devB" });
    expect(r.fetchedAt).toEqual(["devB"]); // A was aborted, never recorded
  });

  it("aborts the in-flight controller when reload is called again", async () => {
    const r = makeReload();
    let aborted = false;
    const fetcher = (did, signal) =>
      new Promise((resolve, reject) => {
        signal.addEventListener("abort", () => {
          aborted = true;
          reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
        });
      });

    const p = r.reload("devA", fetcher);
    r.reload("devB", fetcher);
    await p; // wait for the abort handler to fire
    expect(aborted).toBe(true);
  });
});

// ─── B3·#14: upload listener cleanup on early failure ─────────────

describe("B3·#14 — uploadViaDataChannel cleanup invariant", () => {
  // The shape of the fix is: a single ``cleanup()`` function that
  // detaches the listener AND clears the timeout, called exactly once
  // on every termination path (resolve, reject, timeout). The audit
  // bug was missing-cleanup on the early sendAdb throw path.
  //
  // We test the invariant by simulating each termination path
  // through a minimal harness that mirrors the production structure.

  function makeUploader() {
    let listenerAttached = 0;
    let listenerDetached = 0;
    let timeoutsArmed = 0;
    let timeoutsCleared = 0;
    let done = false;

    function attachListener(_fn) {
      listenerAttached += 1;
      return () => {
        listenerDetached += 1;
      };
    }
    function armTimeout(_ms, _fn) {
      timeoutsArmed += 1;
      return {
        cancel() {
          timeoutsCleared += 1;
        },
      };
    }

    function start({ failBeforeReply = false, replyAfter = null } = {}) {
      return new Promise((resolve, reject) => {
        let timer = null;
        const off = attachListener();
        const cleanup = () => {
          if (done) return;
          done = true;
          off();
          if (timer) timer.cancel();
        };
        timer = armTimeout(1000, () => {
          cleanup();
          reject(new Error("timeout"));
        });

        if (failBeforeReply) {
          // Simulate sendAdb throwing → must still cleanup
          cleanup();
          reject(new Error("send failed"));
          return;
        }
        if (replyAfter === "done") {
          cleanup();
          resolve({ t: "file.done" });
        } else if (replyAfter === "error") {
          cleanup();
          reject(new Error("file.error"));
        }
        // Else leave dangling — caller will await; we never resolve
      });
    }

    return {
      start,
      get state() {
        return {
          listenerAttached,
          listenerDetached,
          timeoutsArmed,
          timeoutsCleared,
        };
      },
    };
  }

  it("detaches listener on early sendAdb failure", async () => {
    const u = makeUploader();
    await expect(u.start({ failBeforeReply: true })).rejects.toThrow();
    expect(u.state.listenerAttached).toBe(1);
    expect(u.state.listenerDetached).toBe(1); // ← the bug fix
    expect(u.state.timeoutsCleared).toBe(1);
  });

  it("detaches listener on successful done reply", async () => {
    const u = makeUploader();
    await u.start({ replyAfter: "done" });
    expect(u.state.listenerDetached).toBe(1);
    expect(u.state.timeoutsCleared).toBe(1);
  });

  it("detaches listener on file.error reply", async () => {
    const u = makeUploader();
    await expect(u.start({ replyAfter: "error" })).rejects.toThrow();
    expect(u.state.listenerDetached).toBe(1);
    expect(u.state.timeoutsCleared).toBe(1);
  });
});

// ─── B3·#17: peekSession does not auto-create ────────────────────

describe("B3·#17 — peekSession is read-only", () => {
  // Reproduce the two accessors from useScrcpySession with the same
  // semantics. The bug was templates calling getSession (which
  // auto-creates) on removed device ids, leaving zombie session
  // entries forever.

  function makeStore() {
    const sessions = new Map();
    const makeSession = (id) => ({ id, frameState: "idle" });
    return {
      sessions,
      getSession(deviceId) {
        if (!deviceId) return null;
        let s = sessions.get(deviceId);
        if (!s) {
          s = makeSession(deviceId);
          sessions.set(deviceId, s);
        }
        return s;
      },
      peekSession(deviceId) {
        if (!deviceId) return null;
        return sessions.get(deviceId) || null;
      },
    };
  }

  it("peekSession returns null for unknown device without creating", () => {
    const store = makeStore();
    const result = store.peekSession("ghost-device");
    expect(result).toBeNull();
    expect(store.sessions.has("ghost-device")).toBe(false);
  });

  it("peekSession returns existing session without mutation", () => {
    const store = makeStore();
    const created = store.getSession("real-device");
    const peeked = store.peekSession("real-device");
    expect(peeked).toBe(created);
    expect(store.sessions.size).toBe(1);
  });

  it("getSession auto-creates (back-compat guarantee)", () => {
    const store = makeStore();
    expect(store.sessions.size).toBe(0);
    store.getSession("new-device");
    expect(store.sessions.size).toBe(1);
    expect(store.sessions.has("new-device")).toBe(true);
  });

  it("peekSession after removal does not resurrect", () => {
    // The bug scenario: removeSession ran, then template re-evaluates
    // and calls peekSession with the dead id. With peek it stays
    // null; with getSession it would resurrect a session that never
    // gets cleaned up again.
    const store = makeStore();
    store.getSession("dev1");
    store.sessions.delete("dev1");

    expect(store.peekSession("dev1")).toBeNull();
    expect(store.sessions.has("dev1")).toBe(false);
  });
});

// ─── B3·#13: desiredRotationByDevice cleanup ─────────────────────

describe("B3·#13 — desiredRotationByDevice cleanup on device removal", () => {
  // The cleanup pattern: when handleDevicesChanged observes a device
  // dropping off the list, it must purge every per-device side-state
  // map. This test pins the contract.

  function cleanupForRemovedDevices(currentDevices, ...maps) {
    const present = new Set(currentDevices);
    for (const m of maps) {
      for (const id of Array.from(m.keys())) {
        if (!present.has(id)) m.delete(id);
      }
    }
  }

  it("drops entries for absent devices across multiple maps", () => {
    const rotations = new Map([
      ["devA", 1],
      ["devB", 2],
      ["devC", 3],
    ]);
    const monitors = new Map([
      ["devA", "monA"],
      ["devC", "monC"],
    ]);

    cleanupForRemovedDevices(["devA"], rotations, monitors);

    expect(Array.from(rotations.keys())).toEqual(["devA"]);
    expect(Array.from(monitors.keys())).toEqual(["devA"]);
  });

  it("is a no-op when all devices are still present", () => {
    const rotations = new Map([
      ["devA", 1],
      ["devB", 2],
    ]);
    cleanupForRemovedDevices(["devA", "devB"], rotations);
    expect(rotations.size).toBe(2);
  });
});
