<script setup lang="ts">
import { computed } from 'vue'
import { timeText, type Holding } from './api'
const props = defineProps<{ holding: Holding }>()
const quoteTime = computed(() => {
  const time = props.holding.quote_time
  return time?.length === 10 ? time : time ? timeText(time) : '尚无行情'
})
const currency: Record<string, string> = { CNY: '元', USD: '美元', HKD: '港元' }
</script>

<template>
  <div class="quoteLine">
    <strong v-if="holding.price != null">{{ holding.price.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) }} {{ currency[holding.currency || ''] }}</strong>
    <span v-if="holding.change_pct != null">{{ holding.change_pct > 0 ? '+' : '' }}{{ holding.change_pct.toFixed(2) }}%</span>
    <small>{{ holding.price_context === 'INTRADAY' ? '盘中暂定' : holding.price_context === 'LAST_CLOSE' ? '最近有效收盘' : '正式收盘' }} · {{ quoteTime }}</small>
  </div>
</template>
