// Regression tests for the refresh-recovery black-screen hypothesis.
//
// The production path differs between a normal user-started stream and a
// page-refresh restore:
//   - normal start usually has a user gesture in the path somewhere;
//   - refresh restore goes through autoRestoreStreams() and binds the
//     existing MediaStream back onto DeviceScreen without any gesture.
//
// DeviceScreen.bindStream() currently does:
//   el.srcObject = stream
//   void el.play().catch(() => {})
//
// That is fine when autoplay is allowed, but if the browser blocks
// unmuted autoplay on a refresh restore, the rejection is swallowed and
// the UI can stay black with no retry signal.

import { describe, expect, it, vi } from "vitest";

function bindStream(el, stream) {
  if (el.srcObject !== stream) {
    el.srcObject = stream || null;
  }
  if (stream) void el.play?.().catch(() => {});
}

function makeVideoElement({
  muted = false,
  autoplayPolicy = "allow",
  onFirstFrame = null,
} = {}) {
  return {
    muted,
    srcObject: null,
    play: vi.fn(() => {
      if (autoplayPolicy === "block" && !muted) {
        return Promise.reject(
          Object.assign(new Error("autoplay blocked"), {
            name: "NotAllowedError",
          }),
        );
      }
      return Promise.resolve().then(() => {
        if (typeof onFirstFrame === "function") onFirstFrame();
      });
    }),
  };
}

describe("B7·#18 — refresh recovery black-screen hypothesis", () => {
  it("refresh-style unmuted binding can reject play() and the rejection is swallowed", async () => {
    const stream = { id: "stream-1" };
    let firstFrameCount = 0;
    const video = makeVideoElement({
      muted: false,
      autoplayPolicy: "block",
      onFirstFrame: () => {
        firstFrameCount += 1;
      },
    });

    bindStream(video, stream);
    await Promise.resolve();
    await Promise.resolve();

    expect(video.srcObject).toBe(stream);
    expect(video.play).toHaveBeenCalledTimes(1);
    await expect(video.play.mock.results[0].value).rejects.toMatchObject({
      name: "NotAllowedError",
    });
    expect(firstFrameCount).toBe(0);
  });

  it("muted playback can advance to a first frame", async () => {
    const stream = { id: "stream-2" };
    let firstFrameCount = 0;
    const video = makeVideoElement({
      muted: true,
      autoplayPolicy: "block",
      onFirstFrame: () => {
        firstFrameCount += 1;
      },
    });

    bindStream(video, stream);
    await Promise.resolve();
    await Promise.resolve();

    expect(video.srcObject).toBe(stream);
    expect(video.play).toHaveBeenCalledTimes(1);
    expect(firstFrameCount).toBe(1);
  });
});
