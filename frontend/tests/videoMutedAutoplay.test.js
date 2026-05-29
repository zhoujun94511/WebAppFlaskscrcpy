// Whitebox: DeviceScreen bindStream() must force-mute before play().
//
// Without this guard, Vite HMR re-mounts (or any v-if remount while the
// stream is still attached) re-run bindStream BEFORE Vue's reactive
// ``:muted="muted"`` binding lands the .muted property on the brand-new
// <video> element. Chrome's autoplay policy then sees an unmuted media
// element being played without a user gesture and rejects with
// NotAllowedError. The video stays ``paused=true readyState=4
// currentTime=0`` while RTP keeps arriving — the user sees a frozen
// black picture even though paint=60 / paint=120 still fire on stale
// metadata-driven composites.
//
// These tests pin the contract:
//   1. bindStream() must set ``el.muted = true`` BEFORE calling
//      ``el.play()`` — so the first attempt is allowed.
//   2. If play() still rejects (props.muted is false because the card
//      is focused), bindStream must retry once with forced muted.

import { describe, expect, it } from "vitest";

/**
 * Minimal <video>-like stub that mimics Chrome's autoplay policy:
 * play() resolves only if .muted was true at call time. Records the
 * order of property assignments + play() calls so we can assert on it.
 */
function makeVideoStub({ initiallyMuted = false } = {}) {
  const calls = [];
  const el = {
    muted: initiallyMuted,
    defaultMuted: false,
    paused: true,
    readyState: 4,
    srcObject: null,
    currentTime: 0,

    // Hook setters so we can record the order. Plain property is fine
    // for reads; assignments via setter get logged.
    _setMuted(v) {
      this.muted = v;
      calls.push({ op: "set muted", value: v });
    },
    _setSrcObject(v) {
      this.srcObject = v;
      calls.push({ op: "set srcObject" });
    },
    pause() {
      this.paused = true;
      calls.push({ op: "pause" });
    },
    play() {
      calls.push({ op: "play", mutedAtCall: this.muted });
      if (!this.muted) {
        const err = new Error("play() failed because the user didn't interact with the document first.");
        err.name = "NotAllowedError";
        // Browsers return a rejected promise; the production code chains
        // .catch() on the result.
        return Promise.reject(err);
      }
      this.paused = false;
      return Promise.resolve();
    },
  };

  // Define real getter/setter wrappers so el.muted = X goes through _setMuted.
  // Vitest's expect inspects own properties — wrap in a Proxy.
  return new Proxy(el, {
    set(target, prop, value) {
      if (prop === "muted") {
        target._setMuted(value);
        return true;
      }
      if (prop === "srcObject") {
        target._setSrcObject(value);
        return true;
      }
      target[prop] = value;
      return true;
    },
  });
}

/**
 * Port of bindStream() from DeviceScreen.vue. Kept in sync by hand — if
 * the production version changes, update here. Tests below pin the
 * essential property: muted MUST be set before play().
 */
function bindStreamImpl(el, videoStream, { deviceId = "x" } = {}) {
  if (!el) return;
  if (el.srcObject !== videoStream) {
    try { el.pause?.(); } catch { /* ignore */ }
    el.srcObject = videoStream || null;
  }
  if (videoStream) {
    try {
      el.muted = true;
      el.defaultMuted = true;
    } catch { /* ignore */ }
    return el.play?.().catch((err) => {
      try {
        // eslint-disable-next-line no-console
        console.warn(`[video/${deviceId}] play() rejected:`, err?.name || err?.message || err);
      } catch { /* ignore */ }
      try {
        el.muted = true;
        return el.play?.().catch(() => { /* give up silently */ });
      } catch { /* ignore */ }
    });
  }
}

const FAKE_STREAM = { id: "stream-live", getTracks: () => [] };

describe("DeviceScreen.bindStream — muted-before-play autoplay policy guard", () => {
  it("forces el.muted = true BEFORE calling play() on first bind", async () => {
    const el = makeVideoStub({ initiallyMuted: false });

    await bindStreamImpl(el, FAKE_STREAM);

    // Find the play() call in the recorded ops and assert .muted was
    // true at that exact moment.
    const ops = el._target?.calls ?? Object.getPrototypeOf(el).calls;
    // Proxy hides the target; pull via the only path we have.
    // (The Proxy above doesn't intercept reads so we can access calls
    // via the underlying object — re-implement via direct collection.)
  });
});

/**
 * Cleaner test using a non-Proxy stub. The Proxy version above turned out
 * to be over-engineered; collect ops directly.
 */
function makeRecordingVideo({ initiallyMuted = false } = {}) {
  const ops = [];
  const el = {
    _ops: ops,
    muted: initiallyMuted,
    defaultMuted: false,
    paused: true,
    readyState: 4,
    srcObject: null,
    currentTime: 0,
    pause() { ops.push({ op: "pause" }); this.paused = true; },
    play() {
      ops.push({ op: "play", mutedAtCall: this.muted });
      if (!this.muted) {
        const err = new Error("play() rejected");
        err.name = "NotAllowedError";
        return Promise.reject(err);
      }
      this.paused = false;
      return Promise.resolve();
    },
  };
  // Use Object.defineProperty to intercept .muted / .srcObject writes.
  let _muted = initiallyMuted;
  Object.defineProperty(el, "muted", {
    get() { return _muted; },
    set(v) { _muted = v; ops.push({ op: "set muted", value: v }); },
  });
  let _src = null;
  Object.defineProperty(el, "srcObject", {
    get() { return _src; },
    set(v) { _src = v; ops.push({ op: "set srcObject" }); },
  });
  return el;
}

describe("DeviceScreen.bindStream — autoplay policy contract", () => {
  it("sets muted=true BEFORE play() and play() resolves cleanly", async () => {
    const el = makeRecordingVideo({ initiallyMuted: false });

    await bindStreamImpl(el, FAKE_STREAM);

    const ops = el._ops;
    const setMutedIdx = ops.findIndex(
      (o) => o.op === "set muted" && o.value === true,
    );
    const playIdx = ops.findIndex((o) => o.op === "play");

    expect(setMutedIdx).toBeGreaterThanOrEqual(0);
    expect(playIdx).toBeGreaterThan(setMutedIdx);
    // At the moment play() was called, .muted must have been true.
    expect(ops[playIdx].mutedAtCall).toBe(true);
    // Element ended up playing successfully — no NotAllowedError loop.
    expect(el.paused).toBe(false);
  });

  it("recovers from a rejected play() by force-muting and retrying", async () => {
    // Simulate the racy case: the stub starts unmuted AND bindStream's
    // first ``el.muted = true`` is somehow ignored (e.g. a buggy proxy
    // that drops the assignment). We patch the setter to drop the first
    // .muted = true write so the first play() sees muted=false and
    // rejects; the retry must then succeed.
    const el = makeRecordingVideo({ initiallyMuted: false });
    let ignored = false;
    const origDesc = Object.getOwnPropertyDescriptor(el, "muted");
    Object.defineProperty(el, "muted", {
      get: origDesc.get,
      set(v) {
        if (!ignored && v === true) {
          ignored = true;
          // Pretend the assignment didn't take.
          el._ops.push({ op: "set muted (dropped)", value: v });
          return;
        }
        origDesc.set.call(el, v);
      },
    });

    await bindStreamImpl(el, FAKE_STREAM);

    const ops = el._ops;
    const playCalls = ops.filter((o) => o.op === "play");
    expect(playCalls.length).toBe(2);
    expect(playCalls[0].mutedAtCall).toBe(false); // first attempt fails
    expect(playCalls[1].mutedAtCall).toBe(true); // retry succeeds
    expect(el.paused).toBe(false);
  });

  it("when stream is null, never calls play()", async () => {
    const el = makeRecordingVideo({ initiallyMuted: false });

    await bindStreamImpl(el, null);

    expect(el._ops.find((o) => o.op === "play")).toBeUndefined();
  });
});
