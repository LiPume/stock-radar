<script setup lang="ts">
import { timeText, sourceName, type Health } from './api'
defineProps<{ health: Health }>()
const states: Record<string,string> = { SENT:'已发送', FAILED:'失败', SENDING:'投递中', UNKNOWN:'回执未知', SKIPPED:'已抑制', PENDING:'待发送', NOT_GENERATED:'尚未生成', DISABLED:'已关闭', NOT_DUE:'未到时间', NONE:'尚未生成' }
</script>
<template>
  <template v-if="health.plan">
    <div class="line"><span>下次自动检测</span><b>{{ health.scheduler_enabled && health.plan.next_scan_at ? timeText(health.plan.next_scan_at) : '暂无计划' }}</b></div>
    <div class="line"><span>下次简报</span><b>{{ health.scheduler_enabled && health.plan.next_brief_at ? timeText(health.plan.next_brief_at) : '暂无计划' }}</b></div>
    <div v-if="health.runs_today" class="line"><span>今日计划 / 实际（含手动）</span><b>{{ health.plan.planned_today }} / {{ health.runs_today.actual }}</b></div>
    <p v-if="health.runs_today" class="meta">成功 {{ health.runs_today.success }} · 部分异常 {{ health.runs_today.partial }} · 失败 {{ health.runs_today.failed }}</p>
    <template v-if="health.messages">
      <div class="line"><span>今日简报</span><b>{{ states[health.messages.brief_status] || health.messages.brief_status || '尚未生成' }}</b></div>
      <div class="line"><span>今日微信</span><b>已用 {{ health.messages.used }} · 剩余 {{ health.messages.remaining }}</b></div>
    </template>
    <details class="usageDetails">
      <summary>行情源与 API 用量</summary>
      <div v-for="(source, name) in health.sources" :key="name">
        <div class="line"><span>{{ sourceName(String(name)) }}</span><b>今日 {{ source.today.http_requests }} · 本月 {{ source.month.http_requests }}</b></div>
        <p class="meta">{{ source.pause_reason || (source.today.failures ? '今日有失败请求' : source.today.successes ? '已成功取数' : '今日暂无请求') }} · 缓存命中 {{ source.today.cache_hits }}</p>
      </div>
    </details>
  </template>
</template>
