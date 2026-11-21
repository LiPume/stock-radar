<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { CandlestickChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components'
import { maFields, numberText, type MAField, type Bar } from './api'

use([CandlestickChart, LineChart, CanvasRenderer, GridComponent, TooltipComponent, MarkLineComponent])
const props = defineProps<{ bars: Bar[] }>()
const el = ref<HTMLElement>()
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
const periods = [5, 10, 20, 30, 60, 120]
const range = ref(20)
const rsiFields = ['rsi6', 'rsi12', 'rsi24'] as const
type LineField = MAField | (typeof rsiFields)[number]
const colors: Record<LineField, string> = {
  ma5: '#177493', ma10: '#ad5e0c', ma20: '#864c92',
  rsi6: '#144fb4', rsi12: '#aa5d0d', rsi24: '#6941a8',
}
const hidden = ref(new Set<LineField>())
function toggle(field: LineField) {
  if (hidden.value.has(field)) hidden.value.delete(field)
  else hidden.value.add(field)
}
function label(field: LineField) {
  return field.startsWith('ma') ? field.slice(2) + '日线' : field.slice(3) + '日'
}
function draw() {
  if (!chart) return
  const rows = props.bars.slice(-range.value)
  const dates = rows.map((r) => r.bar_date)
  const lines = (fields: readonly LineField[], grid: number) =>
    fields.filter((field) => !hidden.value.has(field)).map((field) => ({
      name: label(field),
      type: 'line',
      xAxisIndex: grid,
      yAxisIndex: grid,
      symbol: 'none',
      connectNulls: false,
      data: rows.map((r) => r[field] ?? null),
      lineStyle: { width: 1.7, color: colors[field] },
      itemStyle: { color: colors[field] },
    }))
  chart.setOption({
    animation: false,
    textStyle: { fontFamily: 'system-ui' },
    tooltip: {
      trigger: 'axis', renderMode: 'richText', confine: true,
      formatter: (params: { dataIndex?: number } | { dataIndex?: number }[]) => {
        const index = (Array.isArray(params) ? params[0] : params)?.dataIndex
        const bar = index == null ? undefined : rows[index]
        if (!bar) return ''
        const shown = (fields: readonly LineField[]) => fields.filter((f) => !hidden.value.has(f))
          .map((f) => label(f) + ' ' + numberText(bar[f])).join('  ')
        const rsiValues = shown(rsiFields)
        return [bar.bar_date, '开盘价 ' + numberText(bar.open) + '   收盘价 ' + numberText(bar.close),
          '最高价 ' + numberText(bar.high) + '   最低价 ' + numberText(bar.low),
          shown(maFields), rsiValues && '相对强弱指标', rsiValues].filter(Boolean).join('\n')
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 53, right: 10, top: 9, height: '41%' },
      { left: 53, right: 10, top: '64%', bottom: 32 },
    ],
    xAxis: [0, 1].map((i) => ({
      type: 'category', gridIndex: i, data: dates, boundaryGap: true,
      axisLine: { lineStyle: { color: '#cbd7e4' } },
      axisTick: { show: false },
      axisLabel: { show: i === 1, color: '#536579', hideOverlap: true, formatter: (s: string) => s.slice(5) },
    })),
    yAxis: [
      { type: 'value', scale: true, splitNumber: 4 },
      { type: 'value', min: 0, max: 100, interval: 20, gridIndex: 1 },
    ].map((a) => ({
      ...a,
      axisLabel: { fontSize: 10, color: '#536579', formatter: (n: number) => numberText(n) },
      splitLine: { lineStyle: { color: '#e8eef3' } },
    })),
    series: [
      {
        name: '日线蜡烛图', type: 'candlestick', data: rows.map((r) => [r.open, r.close, r.low, r.high]),
        itemStyle: { color: '#266a80', borderColor: '#266a80', color0: '#fff', borderColor0: '#6c7887' },
      },
      ...lines(maFields, 0),
      ...lines(rsiFields, 1),
      {
        name: '相对强弱指标参考线', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: [], silent: true,
        markLine: {
          symbol: 'none', silent: true, label: { show: false },
          lineStyle: { color: '#93a1b0', type: 'dashed' }, data: [{ yAxis: 20 }, { yAxis: 80 }],
        },
      },
    ],
  }, true)
}
onMounted(() => {
  if (!el.value) return
  chart = init(el.value)
  draw()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})
watch([() => props.bars, range, () => [...hidden.value]], draw)
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <section class="panel chart">
    <div class="sectionTitle">
      <h2>日K与相对强弱指标</h2>
    </div>
    <div class="chartRange">
      <strong>图表区间</strong>
      <div class="seg chartRangeButtons" role="group" aria-label="图表区间（交易日）">
        <button v-for="n in periods" :key="n" type="button" :class="{ chosen: range === n }"
                :aria-pressed="range === n" @click="range = n">近{{ n }}日</button>
      </div>
    </div>
    <div class="chartKeys" role="group" aria-label="切换收盘均线">
      <strong>收盘均线</strong>
      <button v-for="field in maFields" :key="field" type="button" :aria-pressed="!hidden.has(field)"
              :style="{ '--line-color': colors[field] }" @click="toggle(field)">
        {{ label(field) }}
      </button>
    </div>
    <div class="chartPlot">
      <div ref="el" class="chartCanvas" role="img"
           :aria-label="'最近 ' + Math.min(range, bars.length) + ' 个交易日的收盘蜡烛图、均线与相对强弱指标'"></div>
      <div class="chartKeys chartRsiKeys" role="group" aria-label="切换相对强弱指标曲线，虚线表示 20 和 80">
        <strong>相对强弱指标</strong>
        <button v-for="field in rsiFields" :key="field" type="button" :aria-pressed="!hidden.has(field)"
                :style="{ '--line-color': colors[field] }" @click="toggle(field)">
          {{ field.slice(3) }}日
        </button>
      </div>
    </div>
  </section>
</template>
