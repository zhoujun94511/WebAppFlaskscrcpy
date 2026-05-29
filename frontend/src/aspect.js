// Shared aspect-ratio helper for device screen/card sizing.
//
// Returns w/h as a finite ratio, or 0 when the inputs are unusable
// (missing, zero, or wildly out of range — a sanity clamp so a bogus
// ``wm size`` / metadata value can't squash a card into a sliver or a
// pancake). Both DeviceScreen (inner video pane aspect) and DeviceCard
// (the ``--card-aspect`` root var that drives the single-stage width
// formula) consume this, so it lives in one place.
export function safeRatio(w, h) {
  const ww = Number(w) || 0;
  const hh = Number(h) || 0;
  if (!ww || !hh) return 0;
  const r = ww / hh;
  if (r < 0.25 || r > 4) return 0;
  return r;
}
