/**
 * WebRTC client: per-device peer-connection pool.
 *
 * One ``useWebRTC`` instance per browser tab. Internally maintains a
 * ``Map<deviceId, PeerEntry>`` so the user can stream multiple devices
 * concurrently. Each entry owns one RTCPeerConnection, its media stream,
 * and its input + adb DataChannels.
 *
 * Public API:
 *   const rtc = useWebRTC({ socket })
 *
 *   await rtc.addPeer(deviceId)             // negotiate offer/answer
 *   await rtc.removePeer(deviceId)          // tear down one device
 *   await rtc.removeAll()                   // tear down everything (unmount)
 *
 *   rtc.getPeer(deviceId)                   // → PeerView | null  (reactive)
 *     ├ connectionState  ref<'connecting'|'connected'|'failed'|'idle'>
 *     ├ mediaStream      ref<MediaStream|null>
 *     ├ inputChannel     ref<RTCDataChannel|null>
 *     └ adbChannel       ref<RTCDataChannel|null>
 *
 *   rtc.hasPeer(deviceId) → boolean
 *   rtc.activeDeviceIds  ref<string[]>      // reactive list, for v-for
 *
 *   rtc.sendInput(deviceId, evt)            // ⤳ that device's input chan
 *   rtc.sendAdb(deviceId, evt)              // ⤳ that device's adb chan
 *   rtc.on(event, fn)                       // event payload always carries deviceId
 *
 * Signaling protocol (matches backend after Phase A):
 *   webrtc:offer    { device_id, sdp }
 *   webrtc:answer   { device_id, sdp, type }
 *   webrtc:ice      { device_id, candidate, sdpMid, sdpMLineIndex }
 *   webrtc:close    { device_id }
 *   webrtc:closed   { device_id, reason }
 */

import { onBeforeUnmount, ref, shallowReactive } from "vue";

const RTC_CONFIG = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  bundlePolicy: "max-bundle",
  rtcpMuxPolicy: "require",
};

export function useWebRTC({ socket }) {
  // PeerEntry per deviceId. shallowReactive so adding/removing keys triggers
  // reactivity on consumers that iterate the map, but individual entries
  // (which are plain objects of refs) keep their identity stable.
  const peers = shallowReactive(new Map());
  const activeDeviceIds = ref([]); // mirror of peers.keys() — for v-for
  const lastError = ref(null);

  const listeners = new Map(); // event → Set<fn>

  function on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => listeners.get(event)?.delete(fn);
  }

  function fire(event, payload) {
    const set = listeners.get(event);
    if (!set) return;
    for (const fn of set) {
      try {
        fn(payload);
      } catch (err) {
        console.error("[webrtc] listener error", err);
      }
    }
  }

  function syncActiveIds() {
    activeDeviceIds.value = Array.from(peers.keys());
  }

  // ────────────────────────────────────────────────────────────────────
  // Socket binding — register handlers ONCE per socket instance and demux
  // incoming events to the right PeerEntry via the device_id field added
  // by Phase A signaling.
  // ────────────────────────────────────────────────────────────────────
  let boundSocket = null;
  function ensureSocketBindings() {
    const s = socket?.value;
    if (!s || s === boundSocket) return;
    if (boundSocket) {
      boundSocket.off("webrtc:answer");
      boundSocket.off("webrtc:ice");
      boundSocket.off("webrtc:closed");
    }
    boundSocket = s;

    s.on("webrtc:answer", async (data) => {
      const id = data?.device_id;
      const entry = id ? peers.get(id) : null;
      if (!entry) return;
      try {
        await entry.pc.setRemoteDescription(new RTCSessionDescription(data));
      } catch (err) {
        console.error(`[webrtc/${id}] setRemoteDescription failed`, err);
        lastError.value = err;
        entry.connectionState.value = "failed";
      }
    });

    s.on("webrtc:ice", async (data) => {
      const id = data?.device_id;
      const entry = id ? peers.get(id) : null;
      if (!entry || !data?.candidate) return;
      try {
        await entry.pc.addIceCandidate(new RTCIceCandidate(data));
      } catch (err) {
        console.warn(`[webrtc/${id}] addIceCandidate failed`, err);
      }
    });

    s.on("webrtc:closed", (data) => {
      const id = data?.device_id;
      console.warn(`[webrtc/${id || "?"}] server closed PC:`, data?.reason);
      if (id) teardownPeer(id, "closed-by-server");
    });
  }

  // ────────────────────────────────────────────────────────────────────
  // PeerEntry lifecycle
  // ────────────────────────────────────────────────────────────────────
  function makeEntry(deviceId) {
    const pc = new RTCPeerConnection(RTC_CONFIG);
    const entry = {
      deviceId,
      pc,
      mediaStream: ref(null),
      connectionState: ref("connecting"),
      inputChannel: ref(null),
      adbChannel: ref(null),
    };

    pc.addEventListener("icecandidate", (event) => {
      const sock = socket?.value;
      if (event.candidate && sock) {
        sock.emit("webrtc:ice", {
          device_id: deviceId,
          candidate: event.candidate.candidate,
          sdpMid: event.candidate.sdpMid,
          sdpMLineIndex: event.candidate.sdpMLineIndex,
        });
      }
    });

    pc.addEventListener("track", (event) => {
      // Keep MediaStream identity stable across track events whenever
      // possible. Re-assigning ``mediaStream.value`` to a new object
      // makes DeviceCard's watcher re-srcObject the <video> element,
      // which on rotation / IDR reset causes a visible re-bind blip.
      //
      // Cases:
      //   1. event has stream(s) AND our current stream already
      //      contains the new track → no-op.
      //   2. event has stream(s) AND our current stream is missing
      //      it → add the new track in-place, keep identity.
      //   3. event has stream(s) AND we have no stream yet → adopt
      //      the first one as-is.
      //   4. event has no streams (rare) → add the bare track to
      //      our existing or a fresh stream.
      const [incoming] = event.streams;
      const existing = entry.mediaStream.value;

      if (incoming) {
        if (!existing) {
          entry.mediaStream.value = incoming;
        } else if (existing === incoming) {
          // same stream object — nothing to do
        } else {
          // Different stream identity but we already have one. Fold
          // the new track into the existing stream so consumers don't
          // re-bind. Skip if the track is already there.
          const has = existing.getTracks().some((t) => t.id === event.track.id);
          if (!has) existing.addTrack(event.track);
        }
      } else {
        const ms = existing || new MediaStream();
        const has = ms.getTracks().some((t) => t.id === event.track.id);
        if (!has) ms.addTrack(event.track);
        if (!existing) entry.mediaStream.value = ms;
      }
    });

    pc.addEventListener("connectionstatechange", () => {
      const state = pc.connectionState;
      entry.connectionState.value =
        state === "connected"
          ? "connected"
          : state === "connecting"
            ? "connecting"
            : state === "failed"
              ? "failed"
              : state === "closed" || state === "disconnected"
                ? "idle"
                : entry.connectionState.value;
      if (state === "failed" || state === "closed") {
        teardownPeer(deviceId, state);
      }
    });

    pc.addEventListener("datachannel", (event) => {
      const ch = event.channel;
      if (ch.label === "input") {
        entry.inputChannel.value = ch;
      } else if (ch.label === "adb") {
        entry.adbChannel.value = ch;
        ch.addEventListener("message", (evt) => {
          let parsed;
          try {
            parsed = JSON.parse(evt.data);
          } catch {
            parsed = evt.data;
          }
          fire("adb", { deviceId, payload: parsed });
        });
      }
    });

    return entry;
  }

  async function addPeer(deviceId) {
    if (!deviceId) throw new Error("addPeer requires deviceId");
    if (!socket?.value) throw new Error("socket.io not initialised");
    if (peers.has(deviceId)) return peers.get(deviceId);

    ensureSocketBindings();
    const entry = makeEntry(deviceId);
    peers.set(deviceId, entry);
    syncActiveIds();

    // Client bootstrap channel (forces SCTP negotiation before server's
    // channels arrive). Server's labels match — aiortc reuses negotiated ones.
    const local = entry.pc.createDataChannel("client-bootstrap", {
      negotiated: false,
    });
    local.addEventListener("open", () => local.close());

    entry.pc.addTransceiver("video", { direction: "recvonly" });

    try {
      const offer = await entry.pc.createOffer();
      await entry.pc.setLocalDescription(offer);
      socket.value.emit("webrtc:offer", {
        device_id: deviceId,
        sdp: offer.sdp,
      });
    } catch (err) {
      console.error(`[webrtc/${deviceId}] offer failed`, err);
      lastError.value = err;
      entry.connectionState.value = "failed";
      teardownPeer(deviceId, "offer-failed");
      throw err;
    }
    return entry;
  }

  async function removePeer(deviceId, { notifyServer = true } = {}) {
    if (!peers.has(deviceId)) return;
    const sock = socket?.value;
    if (notifyServer && sock) sock.emit("webrtc:close", { device_id: deviceId });
    teardownPeer(deviceId, "removed");
  }

  async function removeAll() {
    const ids = Array.from(peers.keys());
    for (const id of ids) {
      await removePeer(id);
    }
  }

  function teardownPeer(deviceId, reason) {
    const entry = peers.get(deviceId);
    if (!entry) return;
    if (entry.inputChannel.value) {
      try {
        entry.inputChannel.value.close();
      } catch {
        /* ignore */
      }
      entry.inputChannel.value = null;
    }
    if (entry.adbChannel.value) {
      try {
        entry.adbChannel.value.close();
      } catch {
        /* ignore */
      }
      entry.adbChannel.value = null;
    }
    try {
      entry.pc.close();
    } catch {
      /* ignore */
    }
    entry.mediaStream.value = null;
    entry.connectionState.value = "idle";
    peers.delete(deviceId);
    syncActiveIds();
    fire("closed", { deviceId, reason });
  }

  // ────────────────────────────────────────────────────────────────────
  // Per-device send helpers
  // ────────────────────────────────────────────────────────────────────
  function sendInput(deviceId, event) {
    const entry = peers.get(deviceId);
    if (!entry) return false;
    const ch = entry.inputChannel.value;
    if (!ch || ch.readyState !== "open") return false;
    try {
      ch.send(JSON.stringify(event));
      return true;
    } catch (err) {
      console.warn(`[webrtc/${deviceId}] sendInput failed`, err);
      return false;
    }
  }

  function sendAdb(deviceId, event) {
    const entry = peers.get(deviceId);
    if (!entry) return false;
    const ch = entry.adbChannel.value;
    if (!ch || ch.readyState !== "open") return false;
    try {
      ch.send(JSON.stringify(event));
      return true;
    } catch (err) {
      console.warn(`[webrtc/${deviceId}] sendAdb failed`, err);
      return false;
    }
  }

  function getPeer(deviceId) {
    return peers.get(deviceId) || null;
  }

  function hasPeer(deviceId) {
    return peers.has(deviceId);
  }

  onBeforeUnmount(() => {
    void removeAll();
  });

  return {
    // pool ops
    addPeer,
    removePeer,
    removeAll,
    getPeer,
    hasPeer,
    activeDeviceIds,

    // per-device sends
    sendInput,
    sendAdb,

    // events
    on,
    lastError,
  };
}
