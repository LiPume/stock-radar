<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, ruleText, timeText, type Signal } from './api'
const props = defineProps<{ signals: Signal[]; revision: number }>()
type Tab = '收盘信号' | '盘中与确认' | '每日简报' | '微信投递'
const tabs: Tab[] = ['收盘信号', '盘中与确认', '每日简报', '微信投递']
const tab = ref<Tab>('收盘信号')
interface History {
  transitions: {id:number;symbol:string;security_name?:string;stage:string;rule_code:string;created_at:string;evidence:Record<string,unknown>}[]
  briefings: {id:number;trade_date:string;title:string;body:string;delivery_status:string}[]
  deliveries: {id:number;title:string;status:string;reason:string|null;created_at:string;attempts:number}[]
}
const history = ref<History>({ transitions:[], briefings:[], deliveries:[] })
const error = ref(''), loading = ref(false)
const label: Record<string,string> = { PROVISIONAL:'盘中暂定', CLEARED:'信号解除', CONFIRMED:'收盘确认', SENT:'已发送', UNKNOWN:'回执未知', FAILED:'发送失败', SENDING:'发送中', PENDING:'待发送', SKIPPED:'已抑制' }
async function load() {
  loading.value = true
  try { history.value = await api<History>('/history'); error.value = '' }
  catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
onMounted(load)
watch(() => props.revision, load)
</script>
<template>
  <div class="seg historyTabs"><button v-for="t in tabs" :key="t" :class="{ chosen: t === tab }" @click="tab=t">{{ t }}</button></div>
  <template v-if="tab === '收盘信号'">
    <p v-if="!signals.length" class="mutedText">暂无正式收盘信号。</p>
    <article v-for="s in signals" :key="[s.holding_id,s.bar_date,s.rule_code,s.strategy_version].join('-')" class="panel side">
      <strong>{{ ruleText(s.rule_code) }}</strong><p>{{ s.security_name }} {{ s.symbol }} · {{ s.bar_date }}</p>
      <small>RSI6 {{ s.evidence.rsi6 == null ? '—' : Number(s.evidence.rsi6).toFixed(2) }} · RSI12 {{ s.evidence.rsi12 == null ? '—' : Number(s.evidence.rsi12).toFixed(2) }}</small>
    </article>
  </template>
  <p v-else-if="loading" class="mutedText">正在读取记录…</p>
  <p v-else-if="error" class="error">{{ error }} <button @click="load">重试</button></p>
  <template v-else-if="tab==='盘中与确认'">
    <p v-if="!history.transitions.length" class="mutedText">暂无盘中事件。</p>
    <article v-for="e in history.transitions" :key="e.id" class="panel side"><strong>{{ label[e.stage] }} · {{ ruleText(e.rule_code) }}</strong><p>{{ e.security_name }} {{ e.symbol }} · {{ timeText(e.created_at) }}</p><small>RSI6 {{ e.evidence.prev_rsi6 == null ? '—' : Number(e.evidence.prev_rsi6).toFixed(2) }} → {{ e.evidence.rsi6 == null ? '—' : Number(e.evidence.rsi6).toFixed(2) }} · RSI12 {{ e.evidence.prev_rsi12 == null ? '—' : Number(e.evidence.prev_rsi12).toFixed(2) }} → {{ e.evidence.rsi12 == null ? '—' : Number(e.evidence.rsi12).toFixed(2) }}</small></article>
  </template>
  <template v-else-if="tab==='每日简报'">
    <p v-if="!history.briefings.length" class="mutedText">首份交易日简报生成后在这里查看。</p>
    <details v-for="b in history.briefings" :key="b.id" class="panel side"><summary>{{ b.trade_date }} · {{ label[b.delivery_status] || b.delivery_status }}</summary><pre class="briefBody">{{ b.body }}</pre></details>
  </template>
  <template v-else>
    <p v-if="!history.deliveries.length" class="mutedText">暂无投递记录。</p>
    <article v-for="d in history.deliveries" :key="d.id" class="panel side"><strong>{{ d.title || '风险提醒' }}</strong><p>{{ label[d.status] || d.status }} · {{ timeText(d.created_at) }}</p><small v-if="d.reason">{{ d.reason }}</small></article>
  </template>
</template>
