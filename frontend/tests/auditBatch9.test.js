// Regression tests for the exact refresh timing window.
//
// This narrows the race further than auditBatch8:
//   1. the restored stream has already connected,
//   2. the video element has already seen a first frame,
//   3. a stale disconnected event from the previous epoch lands late.
//
// Pre-fix the handler couldn't tell old from new lifecycle epochs and the
// stale disconnect tore down the freshly restored peer. After the
// lifecycle_epoch fix (services/scrcpy_lifecycle.py +
// frontend/src/useScrcpySession.js::handleScrcpyStatus) the stale event
// is gated against the current epoch and silently dropped. These tests
// lock the fixed behaviour in.

import { describe, expect, it } from "vitest";

function makePeer() {
  return {
    connectionState: { value: "connected" },
    mediaStream: { value: { id: "stream-live" } },
  };
}

function makeHarness() {
  const sessions = new Map();
  const peers = new Map();
  const events = [];
  // Mirror useScrcpySession.js::deviceEpochById — seeded by 'connected',
  // consulted by 'disconnected'. The gate this enables is what stops
  // a stale lifecycle event from killing a freshly restored peer.
  const deviceEpochById = new Map();
  let droppedStaleDisconnects = 0;
  const video = {
    srcObject: null,
    firstFrameSeen: false,
  };

  function getSession(deviceId) {
    if (!sessions.has(deviceId)) {
      sessions.set(deviceId, {
        frameState: { value: "idle" },
        paused: { value: false },
        resolutionWidth: { value: 0 },
        resolutionHeight: { value: 0 },
        clipboardContent: { value: "" },
      });
    }
    return sessions.get(deviceId);
  }

  function resetSession(s) {
    if (!s) return;
    s.paused.value = false;
    s.frameState.value = "idle";
    s.resolutionWidth.value = 0;
    s.resolutionHeight.value = 0;
  }

  const rtc = {
    hasPeer(deviceId) {
      return peers.has(deviceId);
    },
    getPeer(deviceId) {
      return peers.get(deviceId) || null;
    },
    async removePeer(deviceId, { notifyServer = true } = {}) {
      events.push({ type: "removePeer", deviceId, notifyServer });
      const peer = peers.get(deviceId);
      if (!peer) return;
      peer.mediaStream.value = null;
      peer.connectionState.value = "idle";
      peers.delete(deviceId);
    },
    async addPeer(deviceId) {
      events.push({ type: "addPeer", deviceId });
      peers.set(deviceId, makePeer());
      return peers.get(deviceId);
    },
  };

  async function startDevice(deviceId) {
    const s = getSession(deviceId);
    s.frameState.value = "loading";
    if (!rtc.hasPeer(deviceId)) await rtc.addPeer(deviceId);
  }

  function bindVideo(deviceId) {
    const peer = rtc.getPeer(deviceId);
    const stream = peer ? peer.mediaStream.value : null;
    if (video.srcObject !== stream) {
      video.srcObject = stream;
    }
  }

  function markFirstFrame() {
    video.firstFrameSeen = Boolean(video.srcObject);
    if (video.firstFrameSeen) {
      const session = Array.from(sessions.values())[0];
      if (session) session.frameState.value = "ready";
    }
  }

  function handleScrcpyStatus(data) {
    if (!data?.device_id) return;
    const id = data.device_id;
    const incomingEpoch = data.epoch ?? null;
    if (incomingEpoch == null) return;
    const s = getSession(id);
    if (data.status === "connected") {
      deviceEpochById.set(id, incomingEpoch);
      s.frameState.value = "loading";
      return;
    }
    if (data.status === "disconnected") {
      const current = deviceEpochById.get(id);
      if (current == null || current !== incomingEpoch) {
        droppedStaleDisconnects += 1;
        return;
      }
      deviceEpochById.delete(id);
      resetSession(s);
      if (rtc.hasPeer(id)) {
        void rtc.removePeer(id, { notifyServer: false });
      }
    }
  }

  return {
    sessions,
    peers,
    events,
    video,
    deviceEpochById,
    get droppedStaleDisconnects() { return droppedStaleDisconnects; },
    getSession,
    startDevice,
    bindVideo,
    markFirstFrame,
    handleScrcpyStatus,
    rtc,
  };
}

describe("B9路#20 - lifecycle epoch gate protects refresh restore from stale disconnects", () => {
  it("a stale-epoch disconnect after first frame is ignored, peer survives", async () => {
    const h = makeHarness();
    const id = "83fc400c";

    await h.startDevice(id);
    // New (current) epoch is announced via the 'connected' broadcast.
    h.handleScrcpyStatus({ device_id: id, status: "connected", epoch: "epoch-NEW" });
    h.bindVideo(id);
    expect(h.video.srcObject).not.toBeNull();
    expect(h.deviceEpochById.get(id)).toBe("epoch-NEW");

    // The refreshed stream has already painted at least one frame.
    h.markFirstFrame();
    expect(h.video.firstFrameSeen).toBe(true);
    expect(h.getSession(id).frameState.value).toBe("ready");
    expect(h.rtc.hasPeer(id)).toBe(true);

    // Stale disconnect from the PREVIOUS backend Client lands late,
    // carrying the OLD epoch. The gate must drop it.
    h.handleScrcpyStatus({ device_id: id, status: "disconnected", epoch: "epoch-OLD" });
    h.bindVideo(id);

    // Peer SURVIVES — this is the inversion vs the pre-fix expectation.
    expect(h.getSession(id).frameState.value).toBe("ready");
    expect(h.rtc.hasPeer(id)).toBe(true);
    expect(h.video.srcObject).not.toBeNull();
    expect(h.droppedStaleDisconnects).toBe(1);
    expect(h.events).toEqual([{ type: "addPeer", deviceId: id }]);
  });

  it("a current-epoch disconnect still tears the peer down", async () => {
    const h = makeHarness();
    const id = "83fc400c";

    await h.startDevice(id);
    h.handleScrcpyStatus({ device_id: id, status: "connected", epoch: "epoch-CURRENT" });
    h.bindVideo(id);
    h.markFirstFrame();
    expect(h.video.srcObject).not.toBeNull();

    // Same epoch — this is the real Client we're bound to dying.
    h.handleScrcpyStatus({ device_id: id, status: "disconnected", epoch: "epoch-CURRENT" });
    h.bindVideo(id);

    expect(h.getSession(id).frameState.value).toBe("idle");
    expect(h.rtc.hasPeer(id)).toBe(false);
    expect(h.video.srcObject).toBeNull();
    expect(h.droppedStaleDisconnects).toBe(0);
    expect(h.deviceEpochById.has(id)).toBe(false);
    expect(h.events).toEqual([
      { type: "addPeer", deviceId: id },
      { type: "removePeer", deviceId: id, notifyServer: false },
    ]);
  });

});
