<script setup lang="ts">
import { checkingVersion, checkVersion, currentBuild, onlineBuild, versionMessage } from './version'
import { timeText } from './api'
defineProps<{ backend?: string }>()
function label(build: typeof currentBuild | null) {
  return build ? build.version + ' · ' + build.commit.slice(0, 8) : '尚未取得'
}
</script>

<template>
  <section class="panel side versionInfo" aria-label="应用版本">
    <div class="sectionTitle">
      <h2>应用版本</h2>
      <button class="secondary" :disabled="checkingVersion" @click="checkVersion(true)">
        {{ checkingVersion ? '正在检查…' : '检查更新' }}
      </button>
    </div>
    <div class="line"><span>当前版本</span><b>{{ label(currentBuild) }}</b></div>
    <div class="line"><span>构建时间</span><b>{{ timeText(currentBuild.builtAt) }}（上海）</b></div>
    <div class="line"><span>线上版本</span><b>{{ label(onlineBuild) }}</b></div>
    <div class="line"><span>后端版本</span><b>{{ backend || '尚未取得' }}</b></div>
    <p v-if="versionMessage" class="meta" role="status">{{ versionMessage }}</p>
  </section>
</template>

<style scoped>
.versionInfo { margin-top: 16px; }
</style>
