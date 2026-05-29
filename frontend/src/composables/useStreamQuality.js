// Per-device WebRTC stream quality monitor.
//
// Polls ``RTCInboundRtpStreamStats`` once per second on the active peer
// connection, looks for three signals of H.264 reference-frame
// divergence ("mosaic"), and asks the backend to force a fresh IDR.
//
// Signals (any one triggers):
//   A  framesDecoded did not advance in the last sample           → freeze
//   B  framesDropped grew by more than 5% of framesDecoded delta  → drops
//   C  freezeCount / totalFreezesDuration grew                    → browser-reported freeze
//
// The backend has its own 8s cooldown per device, so spamming is safe,
// but we also throttle locally to avoid useless DataChannel traffic.
// A 4s local cooldown is comfortably below the server's 8s — the server
// is authoritative.
//
// Design note: this module emits over the *existing* input DataChannel
// (``{ t: "request_keyframe", reason }``). No new pipe, no new
// reconnection path. If the channel isn't open yet (peer still
// negotiating), the request is silently dropped — the next mosaic event
// will retry.

// Plain factory — no Vue setup-context hooks so the caller can attach
// and detach a monitor at any point in a device's lifetime (start /
// stop / restart). Cleanup is the caller's responsibility via the
// returned `stop()`.

const POLL_INTERVAL_MS = 1000;
const LOCAL_COOLDOWN_MS = 4000;

// freeze: 1s with no decoded-frame progress is generous — Chromium under
// load can stall briefly without it being a real glitch. A real
// reference-frame divergence pegs framesDecoded for many seconds.
const FREEZE_NO_PROGRESS_MS = 1500;

// drops: any single tick where >5% of the new frames were dropped is
// almost always macroblock-skip cascade kicking in.
const DROP_RATIO_THRESHOLD = 0.05;

/**
 * Watch a single device's stream quality. Returns `{ stop }`.
 *
 * @param {object} options
 * @param {() => RTCPeerConnection|null} options.getPc       — accessor (PC may reconnect)
 * @param {() => boolean}                 options.isStreaming — only poll while live
 * @param {(reason: string) => void}      options.onTrigger   — called when any signal fires
 */
export function startStreamQuality({ getPc, isStreaming, onTrigger }) {
  let prev = null;
  // ``stallStartTs`` is the timestamp at which framesDecoded first
  // stopped advancing. Tracking it separately from ``prev`` is what
  // makes the freeze detector actually work — using ``prev.ts`` would
  // only ever measure the gap between consecutive polls (≈ 1000 ms),
  // never exceeding the 1500 ms threshold under steady polling.
  // Reset to null on every poll where framesDecoded *did* advance.
  let stallStartTs = null;
  let stopped = false;
  let lastFireTs = 0;

  async function tick() {
    if (stopped) return;
    if (!isStreaming()) {
      prev = null;
      stallStartTs = null;
      return;
    }
    const pc = getPc();
    if (!pc) {
      prev = null;
      stallStartTs = null;
      return;
    }
    let stats;
    try {
      stats = await pc.getStats();
    } catch {
      return;
    }
    let inbound = null;
    stats.forEach((s) => {
      if (s.type === "inbound-rtp" && (s.kind === "video" || s.mediaType === "video")) {
        inbound = s;
      }
    });
    if (!inbound) return;

    const now = performance.now();
    const cur = {
      ts: now,
      framesDecoded: inbound.framesDecoded || 0,
      framesDropped: inbound.framesDropped || 0,
      freezeCount: inbound.freezeCount || 0,
      totalFreezesDuration: inbound.totalFreezesDuration || 0,
    };

    if (!prev) {
      prev = cur;
      return;
    }

    let reason = "";
    const decodedDelta = cur.framesDecoded - prev.framesDecoded;
    const droppedDelta = cur.framesDropped - prev.framesDropped;

    // Signal A: decoded stalled while the stream is supposedly live.
    //
    // Subtle: ``now - prev.ts`` would only measure the gap between two
    // consecutive polls (≈ POLL_INTERVAL_MS), which is below the freeze
    // threshold by construction — the detector would never fire under
    // steady polling. We track the timestamp at which the stall
    // *started* instead, so the elapsed time can grow unbounded across
    // ticks until we see frames again.
    //
    // Tab-throttle guard: when the browser backgrounds this tab,
    // setInterval is clamped (Chrome to ~1Hz, but the previous tick
    // may have been many seconds ago). Without clamping, the very
    // first foreground poll would see a multi-second gap and instantly
    // fire "freeze" — that's not a real stall, just throttling. Cap
    // the implicit stall window contributed by the missed-poll gap to
    // one normal poll interval. The clamp leaves real stalls (which
    // accumulate across multiple successful polls) unaffected.
    if (decodedDelta === 0) {
      if (stallStartTs === null) {
        stallStartTs = Math.max(prev.ts, now - POLL_INTERVAL_MS);
      }
      if (now - stallStartTs >= FREEZE_NO_PROGRESS_MS) {
        reason = "freeze";
      }
    } else {
      stallStartTs = null;
    }

    // Signal B: drop ratio over the threshold.
    if (
      !reason &&
      decodedDelta > 0 &&
      droppedDelta / decodedDelta > DROP_RATIO_THRESHOLD
    ) {
      reason = "drops";
    }

    // Signal C: browser-reported freeze count went up.
    if (
      !reason &&
      cur.freezeCount > prev.freezeCount
    ) {
      reason = "browser-freeze";
    }

    prev = cur;

    if (!reason) return;
    if (now - lastFireTs < LOCAL_COOLDOWN_MS) return;
    // Only burn the cooldown if the trigger actually landed. ``onTrigger``
    // returns a boolean — false means the underlying transport (input
    // DataChannel) wasn't ready, in which case we should be free to
    // retry on the very next poll instead of waiting 4 s for the next
    // window. ``undefined`` returns (callers that don't propagate the
    // signal) are treated as success for backward compatibility.
    let delivered = true;
    try {
      const result = onTrigger(reason);
      if (result === false) delivered = false;
    } catch (err) {
      console.warn("[useStreamQuality] onTrigger error:", err);
      delivered = false;
    }
    if (delivered) lastFireTs = now;
  }

  const timer = setInterval(tick, POLL_INTERVAL_MS);
  return {
    stop() {
      stopped = true;
      clearInterval(timer);
      prev = null;
      stallStartTs = null;
    },
  };
}
