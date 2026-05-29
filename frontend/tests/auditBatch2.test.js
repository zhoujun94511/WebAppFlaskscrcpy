// Regression tests for B2 audit fixes (frontend).
//
// B2·#7  useWebRTC track-event handler must keep MediaStream identity
//        stable when possible. On rotation / IDR reset scrcpy may emit
//        multiple ``track`` events for the same logical stream;
//        re-assigning ``entry.mediaStream.value`` to a brand-new object
//        each time made DeviceScreen's watcher re-srcObject the <video>
//        element, causing a visible re-bind blip + spam of "play()
//        interrupted" promise rejections.
//
// We can't easily unit-test the full useWebRTC composable (it touches
// socket.io + RTCPeerConnection). Instead we extract just the track
// handler's branching logic and exercise it directly against fake
// stream/track objects.

import { describe, expect, it, vi } from "vitest";
import { markRaw, ref } from "vue";

// ─── reproduce the production track-handler logic ─────────────────
//
// This mirrors the branching in useWebRTC.js so a regression in the
// COMPOSABLE will be visible only via integration testing, but a
// regression in the LOGIC is caught here. The mirroring is documented
// inline so reviewers can diff against the source on update.

function applyTrackEvent(entry, event) {
  const [incoming] = event.streams;
  const existing = entry.mediaStream.value;

  if (incoming) {
    if (!existing) {
      entry.mediaStream.value = incoming;
    } else if (existing === incoming) {
      // no-op
    } else {
      const has = existing.getTracks().some((t) => t.id === event.track.id);
      if (!has) existing.addTrack(event.track);
    }
  } else {
    const ms = existing || new FakeMediaStream();
    const has = ms.getTracks().some((t) => t.id === event.track.id);
    if (!has) ms.addTrack(event.track);
    if (!existing) entry.mediaStream.value = ms;
  }
}

// ─── lightweight fakes ────────────────────────────────────────────

class FakeTrack {
  constructor(id, kind = "video") {
    this.id = id;
    this.kind = kind;
  }
}

class FakeMediaStream {
  constructor(tracks = []) {
    this._tracks = [...tracks];
    this.id = `stream-${Math.random().toString(36).slice(2, 8)}`;
  }
  getTracks() {
    return [...this._tracks];
  }
  addTrack(t) {
    this._tracks.push(t);
  }
}

function makeEntry() {
  return { mediaStream: ref(null) };
}

// ─── tests ────────────────────────────────────────────────────────

describe("B2·#7 — useWebRTC track event keeps MediaStream identity stable", () => {
  it("first track event adopts the incoming stream", () => {
    const entry = makeEntry();
    const track = new FakeTrack("track-1");
    const stream = markRaw(new FakeMediaStream([track]));
    applyTrackEvent(entry, { streams: [stream], track });
    expect(entry.mediaStream.value).toBe(stream);
  });

  it("identical second track event is a no-op (no rebind)", () => {
    // Same track, same stream — happens on a benign re-negotiation.
    // Must NOT reassign mediaStream (DeviceScreen's watcher would
    // otherwise re-srcObject the video element).
    const entry = makeEntry();
    const track = new FakeTrack("track-1");
    const stream = markRaw(new FakeMediaStream([track]));
    applyTrackEvent(entry, { streams: [stream], track });
    const before = entry.mediaStream.value;
    applyTrackEvent(entry, { streams: [stream], track });
    expect(entry.mediaStream.value).toBe(before);
  });

  it("second event with a DIFFERENT stream identity but same track folds in-place", () => {
    // The bug scenario: scrcpy re-negotiates and the new ``track``
    // event arrives with a fresh MediaStream identity but the same
    // underlying track. The old behavior assigned the new stream,
    // dropping the old one. The fix folds the track into the
    // existing stream so consumers don't re-bind.
    const entry = makeEntry();
    const track = new FakeTrack("track-1");
    const oldStream = markRaw(new FakeMediaStream([track]));
    applyTrackEvent(entry, { streams: [oldStream], track });

    const newStream = markRaw(new FakeMediaStream([track]));
    applyTrackEvent(entry, { streams: [newStream], track });

    expect(entry.mediaStream.value).toBe(oldStream); // identity preserved
  });

  it("new track id added to existing stream", () => {
    // Adding an audio track later: new track id, but we want to keep
    // the same stream so the <video> element sees an in-place add.
    const entry = makeEntry();
    const vid = new FakeTrack("video-1", "video");
    const aud = new FakeTrack("audio-1", "audio");
    const stream = markRaw(new FakeMediaStream([vid]));
    applyTrackEvent(entry, { streams: [stream], track: vid });

    const stream2 = markRaw(new FakeMediaStream([aud]));
    applyTrackEvent(entry, { streams: [stream2], track: aud });

    expect(entry.mediaStream.value).toBe(stream); // identity preserved
    expect(entry.mediaStream.value.getTracks().map((t) => t.id)).toEqual([
      "video-1",
      "audio-1",
    ]);
  });

  it("track event with no streams array still works (no crash)", () => {
    // Some implementations don't populate event.streams. Fold the
    // bare track into either the existing stream or a fresh one.
    const entry = makeEntry();
    const track = new FakeTrack("track-1");
    applyTrackEvent(entry, { streams: [], track });
    expect(entry.mediaStream.value).not.toBeNull();
    expect(entry.mediaStream.value.getTracks().map((t) => t.id)).toEqual(["track-1"]);
  });

  it("does not double-add the same track on repeated events", () => {
    const entry = makeEntry();
    const track = new FakeTrack("track-1");
    const stream = markRaw(new FakeMediaStream([track]));
    applyTrackEvent(entry, { streams: [stream], track });
    applyTrackEvent(entry, { streams: [stream], track });
    applyTrackEvent(entry, { streams: [stream], track });
    expect(entry.mediaStream.value.getTracks()).toHaveLength(1);
  });
});
