<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { CandlestickChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components'
import type { Bar } from './api'
use([CandlestickChart, LineChart, CanvasRenderer, GridComponent, TooltipComponent, MarkLineComponent])
const props = defineProps<{ bars: Bar[] }>()
const el = ref<HTMLElement>()
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
const periods = [30, 60, 120]
const range = ref(60)
function draw() {
  if (!chart) return
  const rows = props.bars.slice(-range.value)
  const dates = rows.map((r) => r.bar_date)
  const colors = ['#144fb4', '#bd6406', '#6941a8']
  chart.setOption(
    {
      animation: false,
      textStyle: { fontFamily: 'system-ui' },
      tooltip: { trigger: 'axis', renderMode: 'richText', confine: true },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 48, right: 12, top: 18, height: '42%' },
        { left: 48, right: 12, top: '59%', height: '29%' },
      ],
      xAxis: [0, 1].map((i) => ({
        type: 'category',
        gridIndex: i,
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: '#cbd7e4' } },
        axisTick: { show: false },
        axisLabel: {
          show: i === 1,
          color: '#536579',
          hideOverlap: true,
          formatter: (s: string) => s.slice(5),
        },
      })),
      yAxis: [
        { type: 'value', scale: true, splitNumber: 4 },
        { type: 'value', min: 0, max: 100, interval: 20, gridIndex: 1 },
      ].map((a) => ({
        ...a,
        axisLabel: { fontSize: 10, color: '#536579' },
        splitLine: { lineStyle: { color: '#e8eef3' } },
      })),
      series: [
        {
          name: '日 K',
          type: 'candlestick',
          data: rows.map((r) => [r.open, r.close, r.low, r.high]),
          itemStyle: { color: '#266a80', borderColor: '#266a80', color0: '#fff', borderColor0: '#6c7887' },
        },
        ...(['rsi6', 'rsi12', 'rsi24'] as const).map((k, i) => ({
          name: k.toUpperCase(),
          type: 'line',
          xAxisIndex: 1,
          yAxisIndex: 1,
          symbol: 'none',
          data: rows.map((r) => r[k]),
          lineStyle: { width: 2, color: colors[i] },
          itemStyle: { color: colors[i] },
          ...(i === 0
            ? {
                markLine: {
                  symbol: 'none',
                  silent: true,
                  label: { show: false },
                  lineStyle: { color: '#93a1b0', type: 'dashed' },
                  data: [{ yAxis: 20 }, { yAxis: 80 }],
                },
              }
            : {}),
        })),
      ],
    },
    true,
  )
}
onMounted(() => {
  if (!el.value) return
  chart = init(el.value)
  draw()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})
watch([() => props.bars, range], draw)
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <section class="panel chart">
    <div class="sectionTitle">
      <h2>日 K 与 RSI</h2>
      <div class="seg small">
        <button v-for="n in periods" :key="n" :class="{ chosen: range === n }" @click="range = n">
          {{ n }} 日
        </button>
      </div>
    </div>
    <div class="legend">
      <span>RSI6</span><span>RSI12</span><span>RSI24</span><small>参考线 20 / 80</small>
    </div>
    <div
      ref="el"
      style="height: 365px; width: 100%"
      role="img"
      :aria-label="'最近 ' + Math.min(range, bars.length) + ' 个收盘日的日 K 及 RSI 曲线'"
    ></div>
  </section>
</template>
