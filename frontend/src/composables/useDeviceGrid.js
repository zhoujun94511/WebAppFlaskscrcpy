/**
 * Adaptive device-grid layout.
 *
 * Given a reactive device count and a target container element, pick the
 * (cols, rows, cardWidth) triple that maximises the *video* area per card
 * inside the available container, subject to:
 *   - MIN_CARD_W   : never shrink a card below ~260 px wide (illegible)
 *   - MAX_CARD_W   : never stretch a card above ~420 px (a phone-shaped
 *                    card on a 4K screen with a single device should not
 *                    swell into a billboard)
 *   - card-chrome  : head (~40 px) + actions (~50 px) is non-video,
 *                    deducted before evaluating the video area
 *   - phone aspect : the inner video preview is 9:16, so a tall card
 *                    with narrow width gives more video than a short
 *                    wide one — the scoring uses video-area-after-aspect
 *                    not raw cell area
 *
 * Reactive: re-evaluates on ResizeObserver / window resize / device-count
 * change. Returns ``{ cols, rows, cardWidth }`` as a computed ref.
 */

import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const MIN_CARD_W = 260;
const MAX_CARD_W = 420;
const GAP = 18;
const CHROME = 100; // card head + actions vertical budget
const PHONE_ASPECT = 16 / 9; // h / w

export function useDeviceGrid(getCount, getContainer, options = {}) {
  const minW = options.minCardWidth ?? MIN_CARD_W;
  const maxW = options.maxCardWidth ?? MAX_CARD_W;
  const gap = options.gap ?? GAP;

  const containerSize = ref({ w: 0, h: 0 });
  let ro = null;
  let resizeHandler = null;

  function measure() {
    const el = typeof getContainer === "function" ? getContainer() : null;
    if (el) {
      const r = el.getBoundingClientRect();
      containerSize.value = { w: Math.round(r.width), h: Math.round(r.height) };
    } else if (typeof window !== "undefined") {
      containerSize.value = { w: window.innerWidth, h: window.innerHeight };
    }
  }

  // Attach the ResizeObserver to the *current* container element. The
  // grid container is ``v-if``-gated (only present in grid view, not
  // single-stage), so it can appear or disappear after this composable
  // mounts. Pre-fix the RO was bound once at onMounted; switching from
  // single → grid view never re-observed the freshly-mounted container
  // and ``measure()`` stayed stale. Re-binding on every container
  // identity change fixes that.
  let lastObservedEl = null;
  function attachObserver() {
    if (typeof ResizeObserver === "undefined") return;
    const el = typeof getContainer === "function" ? getContainer() : null;
    if (el === lastObservedEl) return; // nothing changed
    if (ro) {
      try {
        ro.disconnect();
      } catch {
        /* ignore */
      }
      ro = null;
    }
    lastObservedEl = el;
    if (el) {
      ro = new ResizeObserver(() => measure());
      ro.observe(el);
    }
  }

  onMounted(() => {
    measure();
    attachObserver();
    if (typeof window !== "undefined") {
      resizeHandler = () => measure();
      window.addEventListener("resize", resizeHandler);
    }
  });

  onBeforeUnmount(() => {
    if (ro) {
      try {
        ro.disconnect();
      } catch {
        /* ignore */
      }
      ro = null;
    }
    if (resizeHandler && typeof window !== "undefined") {
      window.removeEventListener("resize", resizeHandler);
      resizeHandler = null;
    }
  });

  // Re-measure when the count flips between 0 and N (container may have
  // mounted later than the composable, e.g. when devices arrive after
  // initial render). Also re-attach the ResizeObserver in case the
  // container element changed identity (view-mode toggle re-mounts it).
  watch(
    () => (typeof getCount === "function" ? getCount() : 0),
    () => {
      attachObserver();
      measure();
    },
  );

  // Track container identity reactively too — for the view-mode toggle
  // case where the count stays the same but the v-if-gated container
  // appears or disappears.
  watch(
    () => (typeof getContainer === "function" ? getContainer() : null),
    () => {
      attachObserver();
      measure();
    },
  );

  const layout = computed(() => {
    const raw = typeof getCount === "function" ? getCount() : 0;
    const N = Math.max(1, raw | 0);
    const W = containerSize.value.w || 0;
    const H = containerSize.value.h || 0;

    // Before mount or zero-size container — fall back to a sane default
    // (3 cols) so the first paint isn't a one-column stack.
    if (W < 1)
      return { cols: Math.min(N, 3), rows: Math.ceil(N / 3), cardWidth: maxW };

    let best = null;
    const maxColsHard = 6; // beyond this cards get illegible regardless of viewport

    function preferred(a, b) {
      if (a.score !== b.score) return a.score > b.score ? a : b;
      // Same per-card video area? Lay devices in fewer rows — flatter grids
      // are easier to compare at a glance (e.g. 4 devices on 4K is better
      // as 4×1 than 2×2 when each card is already saturated at MAX_CARD_W).
      if (a.rows !== b.rows) return a.rows < b.rows ? a : b;
      // Last tiebreak: fewer empty cells (cols × rows closer to N).
      const emptyA = a.cols * a.rows - N;
      const emptyB = b.cols * b.rows - N;
      return emptyA <= emptyB ? a : b;
    }

    // Dynamic upper bound: on roomy viewports with very few devices, the
    // 420 px cap is far too conservative — a single phone on 1920×1080
    // should swell to ~520 px, on 4K to ~1100 px. We let cardW grow up
    // to the width that keeps the 9:16 video region inside the cell
    // vertically. For N ≥ 3 we keep the original maxW so a card never
    // visually dominates a multi-device grid.
    const usableHForBig = Math.max(220, H - CHROME - 60);
    const dynMaxW = Math.min(usableHForBig / PHONE_ASPECT, W * 0.62);
    const effMaxW = N <= 2 ? Math.max(maxW, dynMaxW) : maxW;

    for (let cols = 1; cols <= Math.min(maxColsHard, N); cols++) {
      const rows = Math.ceil(N / cols);
      const cellW = (W - (cols - 1) * gap) / cols;
      if (cellW < minW) continue;

      const cellH = (H - (rows - 1) * gap) / rows;
      const usableH = Math.max(60, cellH - CHROME);
      const cardW = Math.min(cellW, effMaxW);
      // Video region is bounded by both cell width and phone aspect.
      const videoH = Math.min(usableH, cardW * PHONE_ASPECT);
      const videoW = videoH / PHONE_ASPECT;
      const score = videoW * videoH;

      const candidate = { cols, rows, cardWidth: cardW, score };
      best = best ? preferred(best, candidate) : candidate;
    }

    // No layout satisfied minW — fall back to the smallest acceptable cols.
    if (!best) {
      const cols = Math.max(1, Math.floor((W + gap) / (minW + gap)));
      best = { cols, rows: Math.ceil(N / cols), cardWidth: minW, score: 0 };
    }

    return { cols: best.cols, rows: best.rows, cardWidth: best.cardWidth };
  });

  return { layout, containerSize };
}
