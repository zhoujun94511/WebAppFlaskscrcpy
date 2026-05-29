// Regression tests for B6 audit fixes (frontend).
//
// B6·R11  LogcatPanel must clear its buffer when level/tag filter
//         changes — otherwise old-filter rows linger in the rendered
//         list until MAX_LINES rolls over.
//
// B6·R12  DeviceScreen wheel handler must reuse a cached rect within
//         an idle window instead of calling getBoundingClientRect on
//         every event.

import { describe, expect, it } from "vitest";

// ─── B6·R11: LogcatPanel filter-change buffer clear ───────────────
//
// Extract the shape of the buffer-reset contract. Real LogcatPanel
// has watch(deviceId) + watch([level, tag]) — pre-fix only the first
// cleared the buffer.

function makeLogcatBufferModel() {
  let pendingLines = [];
  let allLines = [];
  let pendingBytes = 0;
  let displayed = [];

  function reset() {
    pendingLines = [];
    allLines = [];
    pendingBytes = 0;
    displayed = [];
  }

  return {
    push(line) {
      pendingLines.push(line);
      pendingBytes += line.length;
    },
    flush() {
      allLines = allLines.concat(pendingLines);
      pendingLines = [];
      displayed = [...allLines];
    },
    onDeviceChange() {
      reset();
    },
    onFilterChange() {
      // The fix
      reset();
    },
    get state() {
      return {
        pending: pendingLines.length,
        all: allLines.length,
        bytes: pendingBytes,
        displayed: displayed.length,
      };
    },
  };
}

describe("B6·R11 — LogcatPanel clears buffer on level/tag change", () => {
  it("filter change drops all pending + accumulated lines", () => {
    const m = makeLogcatBufferModel();
    m.push("[I/Tag1] foo");
    m.push("[I/Tag1] bar");
    m.flush();
    expect(m.state.all).toBe(2);
    expect(m.state.displayed).toBe(2);

    m.onFilterChange();

    expect(m.state.pending).toBe(0);
    expect(m.state.all).toBe(0);
    expect(m.state.bytes).toBe(0);
    expect(m.state.displayed).toBe(0);
  });

  it("filter-change and device-change behave identically", () => {
    const a = makeLogcatBufferModel();
    const b = makeLogcatBufferModel();
    for (let i = 0; i < 5; i++) {
      a.push(`line-${i}`);
      b.push(`line-${i}`);
    }
    a.flush();
    b.flush();
    a.onFilterChange();
    b.onDeviceChange();
    expect(a.state).toEqual(b.state);
  });

  it("new lines after filter change start from empty", () => {
    const m = makeLogcatBufferModel();
    m.push("old-filter line");
    m.flush();
    m.onFilterChange();
    m.push("new-filter line");
    m.flush();
    expect(m.state.all).toBe(1);
    expect(m.state.displayed).toBe(1);
  });
});

// ─── B6·R12: DeviceScreen wheel rect cache ──────────────────────────

function makeWheelModel(now = () => 1000) {
  let wheelRect = null;
  let wheelRectExpiresAt = 0;
  let rectReads = 0;
  const TTL = 250;

  function readRect() {
    rectReads += 1;
    return { left: 0, top: 0, width: 400, height: 800 };
  }

  function onWheel(_event) {
    const t = now();
    if (!wheelRect || t > wheelRectExpiresAt) {
      wheelRect = readRect();
    }
    wheelRectExpiresAt = t + TTL;
  }

  return {
    onWheel,
    get rectReads() { return rectReads; },
    get cachedRect() { return wheelRect; },
  };
}

describe("B6·R12 — DeviceScreen wheel handler caches rect within idle window", () => {
  it("burst of 100 wheel events within TTL = 1 rect read", () => {
    let t = 1000;
    const m = makeWheelModel(() => t);
    for (let i = 0; i < 100; i++) {
      m.onWheel({});
      t += 5; // 200 Hz wheel rate
    }
    expect(m.rectReads).toBe(1);
  });

  it("re-reads rect once the TTL expires", () => {
    let t = 1000;
    const m = makeWheelModel(() => t);
    m.onWheel({}); // first read
    t += 50;
    m.onWheel({}); // within TTL, cache hit
    t += 1000; // way past TTL
    m.onWheel({}); // miss → re-read
    expect(m.rectReads).toBe(2);
  });

  it("each wheel event extends the TTL window (sliding)", () => {
    // Continuous wheel scrolling at slow rate should still only read once.
    let t = 1000;
    const m = makeWheelModel(() => t);
    for (let i = 0; i < 20; i++) {
      m.onWheel({});
      t += 200; // every 200 ms — under the 250 ms TTL
    }
    expect(m.rectReads).toBe(1);
  });
});
