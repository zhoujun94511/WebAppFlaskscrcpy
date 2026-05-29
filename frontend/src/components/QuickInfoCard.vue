<template>
  <section class="quick-card info-card" v-if="deviceId">
    <header class="quick-card-head">
      <strong>{{ info.model || deviceId }}</strong>
      <small v-if="brandLine">{{ brandLine }}</small>
      <span v-if="info.connection_type" class="quick-card-chip" :class="`conn-${info.connection_type}`">
        {{ info.connection_type === 'wifi' ? 'Wi-Fi' : 'USB' }}
      </span>
    </header>

    <div class="metric-grid">
      <MetricTile
        v-if="info.battery_level != null"
        :percent="info.battery_level"
        :tone="batteryClass"
        :primary="`${info.battery_level}%`"
        :label="t('info.battery')"
        :sub="chargingStatus || ' '"
      />
      <MetricTile
        v-if="Number.isFinite(info.battery_temp_c)"
        :percent="tempPercent"
        :tone="tempTone"
        :primary="`${info.battery_temp_c.toFixed(1)}°`"
        :label="t('info.temp')"
        :sub="tempLabel"
        :stroke="tempColor"
        :inline-meta="true"
      />
      <MetricTile
        v-if="info.storage_total_bytes"
        :percent="storagePercent"
        :tone="storageClass"
        :primary="`${storagePercent}%`"
        :label="t('info.storage')"
        :sub="`${fmtBytes(info.storage_used_bytes)} / ${fmtBytes(info.storage_total_bytes)}`"
      />
      <MetricTile
        v-if="info.memory_total_bytes"
        :percent="memoryPercent"
        :tone="memoryClass"
        :primary="`${memoryPercent}%`"
        :label="t('info.memory')"
        :sub="`${fmtBytes(info.memory_used_bytes)} / ${fmtBytes(info.memory_total_bytes)}`"
      />
    </div>

    <dl class="info-table">
      <template v-for="entry in sortedInfoEntries" :key="entry.label">
        <dt>{{ entry.label }}</dt>
        <dd :class="{ mono: entry.mono }">
          <template v-for="(part, idx) in entry.parts" :key="`${entry.label}-${idx}`">
            <span :class="part.className">{{ part.text }}</span>
          </template>
        </dd>
      </template>
    </dl>
  </section>
</template>

<script setup>
import { computed, h, ref, watch } from 'vue'
import { useUiI18n } from '../composables/useUiI18n'

const { t } = useUiI18n()
const props = defineProps({ deviceId: { type: String, default: '' } })

const info = ref({})
function makeEntry(label, parts, mono = false) {
  return {
    label,
    parts,
    mono,
    sortText: parts.map((part) => part.text).join(''),
  }
}

// AbortController for in-flight reloads. Without this, switching
// devices rapidly (user clicking through the strip) lets the previous
// fetch land *after* the new one and overwrite ``info.value`` with
// stale data for a device the user already navigated away from.
// Each reload aborts the previous controller and binds a fresh one.
let inFlight = null

async function reload(deviceId) {
  // Cancel any pending request from a previous deviceId switch.
  if (inFlight) inFlight.abort()
  if (!deviceId) { info.value = {}; inFlight = null; return }
  const controller = new AbortController()
  inFlight = controller
  try {
    const r = await fetch(
      `/api/device/${encodeURIComponent(deviceId)}/info`,
      { signal: controller.signal },
    )
    const data = await r.json().catch(() => ({}))
    // Guard: if another reload superseded us between the await and
    // here, drop the result rather than overwrite the newer one.
    if (controller !== inFlight) return
    info.value = data.info || {}
  } catch (err) {
    // AbortError on cancellation is expected — keep info untouched
    // so the new reload's success path populates it cleanly.
    if (err?.name === 'AbortError') return
    if (controller !== inFlight) return
    info.value = {}
  } finally {
    if (controller === inFlight) inFlight = null
  }
}

watch(() => props.deviceId, reload, { immediate: true })

const sortedInfoEntries = computed(() => {
  const entries = []

  if (info.value.android_version) {
    entries.push(
      makeEntry(t('info.android'), [
        { text: `${info.value.android_version}`, className: '' },
        info.value.sdk
          ? { text: ` · API ${info.value.sdk}`, className: 'subtle' }
          : null,
      ].filter(Boolean)),
    )
  }

  if (info.value.wifi_ssid) {
    entries.push(
      makeEntry(t('info.wifi'), [
        { text: info.value.wifi_ssid, className: '' },
      ]),
    )
  }

  if (info.value.ip_address) {
    entries.push(
      makeEntry(t('info.ip'), [
        { text: info.value.ip_address, className: 'mono' },
      ], true),
    )
  }

  if (socLine.value) {
    entries.push(
      makeEntry(t('info.soc'), [
        socVendor.value ? { text: socVendor.value, className: '' } : null,
        socVendor.value && socModel.value ? { text: ' · ', className: 'subtle' } : null,
        socModel.value ? { text: socModel.value, className: 'mono' } : null,
      ].filter(Boolean)),
    )
  }

  if (info.value.abi) {
    entries.push(
      makeEntry(t('info.build'), [
        { text: info.value.abi, className: 'mono' },
      ], true),
    )
  }

  if (info.value.resolution_width) {
    entries.push(
      makeEntry(t('info.resolution'), [
        {
          text: `${info.value.resolution_width} × ${info.value.resolution_height}`,
          className: 'mono',
        },
        info.value.density_dpi
          ? { text: ` · ${info.value.density_dpi} dpi`, className: 'subtle' }
          : null,
      ].filter(Boolean), true),
    )
  }

  entries.push(
    makeEntry(t('info.serial'), [
      { text: props.deviceId, className: 'mono' },
    ], true),
  )

  return entries.sort((a, b) => {
    const lenDiff = a.sortText.length - b.sortText.length
    if (lenDiff !== 0) return lenDiff
    const firstDiff = a.label.slice(0, 1).localeCompare(b.label.slice(0, 1))
    if (firstDiff !== 0) return firstDiff
    return a.label.localeCompare(b.label)
  })
})
// ── Inline MetricTile component ───────────────────────────────────
// A small dashboard tile: SVG ring with stroke-dasharray as the
// percent fill, big primary number centred, label + sub line below.
// Inlined here (not a separate .vue) because nothing else in the app
// uses this exact shape and the geometry is intimately tied to the
// surrounding 2×2 grid.
const MetricTile = {
  name: 'MetricTile',
  props: {
    percent: { type: Number, default: 0 },
    primary: { type: String, default: '' },
    label: { type: String, default: '' },
    sub: { type: String, default: '' },
    tone: { type: String, default: 'ok' },  // ok | warn | danger
    stroke: { type: String, default: '' },  // explicit override (temp tile)
    inlineMeta: { type: Boolean, default: false },
  },
  setup(p) {
    const RADIUS = 22
    const CIRC = 2 * Math.PI * RADIUS
    const dash = computed(() => {
      const pct = Math.min(100, Math.max(0, Number(p.percent) || 0))
      const filled = (pct / 100) * CIRC
      return `${filled} ${CIRC - filled}`
    })
    const ringColor = computed(() => {
      if (p.stroke) return p.stroke
      if (p.tone === 'danger') return 'var(--metric-danger, #ef4444)'
      if (p.tone === 'warn') return 'var(--metric-warn, #f59e0b)'
      return 'var(--metric-ok, #10b981)'
    })

    return () => h('div', { class: 'metric-tile' }, [
      h('div', { class: 'metric-ring' }, [
        h('svg', {
          viewBox: '0 0 56 56',
          width: '56',
          height: '56',
          class: 'metric-ring-svg',
        }, [
          h('circle', {
            cx: 28, cy: 28, r: RADIUS,
            fill: 'none',
            'stroke-width': 4,
            class: 'metric-ring-track',
          }),
          h('circle', {
            cx: 28, cy: 28, r: RADIUS,
            fill: 'none',
            stroke: ringColor.value,
            'stroke-width': 4,
            'stroke-linecap': 'round',
            'stroke-dasharray': dash.value,
            'stroke-dashoffset': CIRC / 4,  // start at 12 o'clock
            transform: 'rotate(-90 28 28)',
          class: 'metric-ring-fill',
          }),
        ]),
        h('span', { class: 'metric-ring-primary' }, p.primary),
      ]),
      ...(p.inlineMeta
        ? [
            h('div', { class: 'metric-tile-meta metric-tile-meta-inline' }, [
              h('span', { class: 'metric-tile-label' }, p.label),
              h('span', { class: 'metric-tile-sub metric-tile-sub-inline' }, p.sub),
            ]),
          ]
        : [
            h('div', { class: 'metric-tile-label' }, p.label),
            h('div', { class: 'metric-tile-sub' }, p.sub),
          ]),
    ])
  },
}

function fmtBytes(n) {
  if (n == null) return '-'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n, i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`
}

const batteryClass = computed(() => {
  const lvl = info.value.battery_level
  if (lvl == null) return 'ok'
  if (lvl < 20) return 'danger'
  if (lvl < 50) return 'warn'
  return 'ok'
})

const BATTERY_STATUS_KEYS = {
  Charging: 'info.batteryStatus.charging',
  Full: 'info.batteryStatus.full',
  'Not charging': 'info.batteryStatus.notCharging',
}
const chargingStatus = computed(() => {
  const status = info.value.battery_status
  const level = info.value.battery_level
  if (!status || status === 'Discharging' || status === 'Unknown') return ''
  if (level === 100 && status === 'Full') return ''
  const key = BATTERY_STATUS_KEYS[status]
  return key ? t(key) : status
})

const memoryPercent = computed(() => {
  const total = info.value.memory_total_bytes
  const used = info.value.memory_used_bytes
  if (!total || used == null) return 0
  return Math.min(100, Math.max(0, Math.round((used / total) * 100)))
})
const memoryClass = computed(() => {
  const p = memoryPercent.value
  if (p > 85) return 'danger'
  if (p > 70) return 'warn'
  return 'ok'
})

const storagePercent = computed(() => {
  const total = info.value.storage_total_bytes
  const used = info.value.storage_used_bytes
  if (!total || used == null) return 0
  return Math.min(100, Math.max(0, Math.round((used / total) * 100)))
})
const storageClass = computed(() => {
  const p = storagePercent.value
  if (p > 90) return 'danger'
  if (p > 75) return 'warn'
  return 'ok'
})

// Temperature tile: ring fill follows a 0–60 °C scale; ring colour
// is sampled from a 5-stop thermal gradient so the colour itself
// communicates "is this hot?" without the user reading the number.
const tempPercent = computed(() => {
  const v = info.value.battery_temp_c
  if (!Number.isFinite(v)) return 0
  return Math.min(100, Math.max(0, (v / 60) * 100))
})
const tempColor = computed(() => {
  const v = Number(info.value.battery_temp_c)
  if (!Number.isFinite(v)) return '#10b981'
  // Linear-ish stops: 0→#3b82f6, 15→#06b6d4, 30→#10b981, 45→#f59e0b, 60→#ef4444
  if (v < 15) return '#06b6d4'
  if (v < 30) return '#10b981'
  if (v < 40) return '#84cc16'
  if (v < 50) return '#f59e0b'
  return '#ef4444'
})
const tempTone = computed(() => {
  const v = Number(info.value.battery_temp_c)
  if (!Number.isFinite(v)) return 'ok'
  if (v >= 45) return 'danger'
  if (v >= 38) return 'warn'
  return 'ok'
})
const tempLabel = computed(() => {
  const v = Number(info.value.battery_temp_c)
  if (!Number.isFinite(v)) return ''
  if (v >= 45) return t('info.tempHot') || 'Hot'
  if (v >= 38) return t('info.tempWarm') || 'Warm'
  return t('info.tempNormal') || 'Normal'
})

const brandLine = computed(() => {
  const m = info.value.manufacturer || ''
  const b = info.value.brand || ''
  if (b && m) return `${b} · ${m}`
  return b || m
})

const SOC_VENDOR_MAP = {
  QTI: 'Qualcomm',
  'Qualcomm Technologies, Inc.': 'Qualcomm',
  MTK: 'MediaTek',
  Mediatek: 'MediaTek',
  Samsung: 'Samsung',
  Google: 'Google Tensor',
  Unisoc: 'Unisoc',
  HiSilicon: 'HiSilicon',
}
const socVendor = computed(() => {
  const v = info.value.soc_manufacturer || ''
  return SOC_VENDOR_MAP[v] || v
})
const socModel = computed(() => info.value.soc_model || '')
const socLine = computed(() => socVendor.value || socModel.value)
</script>
