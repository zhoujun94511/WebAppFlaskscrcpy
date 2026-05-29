// Regression tests for the refresh-recovery race hypothesis.
//
// If the backend emits a late ``scrcpy_status: disconnected`` for an
// older client after a page refresh has already restored a new stream,
// the current ``handleScrcpyStatus()`` branch will:
//   1. reset the per-device session, and
//   2. tear down the WebRTC peer with ``notifyServer: false``.
//
// That is enough to black out the card even though the new stream had
// already been created.

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
  const deviceEpochById = new Map();

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

  function handleScrcpyStatus(data) {
    if (!data?.device_id) return;
    const id = data.device_id;
    const s = getSession(id);
    const incomingEpoch = data.epoch ?? null;
    if (incomingEpoch == null) return;
    if (data.status === "connected") {
      deviceEpochById.set(id, incomingEpoch);
      s.frameState.value = "loading";
      return;
    }
    if (data.status === "disconnected") {
      const current = deviceEpochById.get(id);
      if (current == null || current !== incomingEpoch) return;
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
    deviceEpochById,
    getSession,
    startDevice,
    handleScrcpyStatus,
    rtc,
  };
}

describe("B8·#19 — lifecycle epoch gate protects refresh restore from stale disconnects", () => {
  it("a stale-epoch disconnect is ignored after the restored peer reconnects", async () => {
    const h = makeHarness();
    const id = "83fc400c";

    await h.startDevice(id);
    h.handleScrcpyStatus({ device_id: id, status: "connected", epoch: "epoch-OLD" });
    expect(h.getSession(id).frameState.value).toBe("loading");
    expect(h.rtc.hasPeer(id)).toBe(true);

    // Refresh restore has already brought up the new peer.
    h.handleScrcpyStatus({ device_id: id, status: "connected", epoch: "epoch-NEW" });
    expect(h.deviceEpochById.get(id)).toBe("epoch-NEW");
    expect(h.rtc.hasPeer(id)).toBe(true);
    expect(h.rtc.getPeer(id).mediaStream.value).not.toBeNull();

    // Late disconnect from the previous epoch must be ignored.
    h.handleScrcpyStatus({ device_id: id, status: "disconnected", epoch: "epoch-OLD" });

    expect(h.getSession(id).frameState.value).toBe("loading");
    expect(h.rtc.hasPeer(id)).toBe(true);
    expect(h.rtc.getPeer(id).mediaStream.value).not.toBeNull();
    expect(h.events).toEqual([{ type: "addPeer", deviceId: id }]);
  });

  it("a current-epoch disconnect still tears down the peer", async () => {
    const h = makeHarness();
    const id = "83fc400c";

    await h.startDevice(id);
    h.handleScrcpyStatus({ device_id: id, status: "connected", epoch: "epoch-CURRENT" });
    expect(h.rtc.hasPeer(id)).toBe(true);

    h.handleScrcpyStatus({
      device_id: id,
      status: "disconnected",
      epoch: "epoch-CURRENT",
    });

    expect(h.getSession(id).frameState.value).toBe("idle");
    expect(h.rtc.hasPeer(id)).toBe(false);
    expect(h.events).toEqual([
      { type: "addPeer", deviceId: id },
      { type: "removePeer", deviceId: id, notifyServer: false },
    ]);
  });
});
