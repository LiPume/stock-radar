<script setup lang="ts">
import { computed } from 'vue'
import { maFields, numberText, timeText, type Holding } from './api'
const props = defineProps<{ holding: Holding }>()
const quoteTime = computed(() => {
  const time = props.holding.quote_time
  return time?.length === 10 ? time : time ? timeText(time) : '尚无行情'
})
const phase = computed(() => {
  if (props.holding.price_context === 'INTRADAY') return '盘中暂定'
  if (props.holding.price_context === 'LAST_CLOSE') return '最近有效收盘'
  return '正式收盘'
})
const currency: Record<string, string> = { CNY: '元', USD: '美元', HKD: '港元' }
</script>

<template>
  <div class="quoteLine">
    <strong v-if="holding.price != null">{{ numberText(holding.price) }} {{ currency[holding.currency || ''] }}</strong>
    <span v-if="holding.change_pct != null">{{ holding.change_pct > 0 ? '+' : '' }}{{ numberText(holding.change_pct) }}%</span>
    <small>{{ phase }} · {{ quoteTime }}</small>
  </div>
  <div class="quoteMeans" :aria-label="phase + '移动均线'">
    <small>{{ holding.price_context === 'INTRADAY' ? '盘中均线' : '收盘均线' }}</small>
    <span v-for="field in maFields" :key="field">
      {{ field.slice(2) }}日 <b>{{ numberText(holding[field]) }}</b>
    </span>
  </div>
</template>
