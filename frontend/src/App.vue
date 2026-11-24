<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Icon as VanIcon, Switch as VanSwitch, showConfirmDialog } from 'vant'
import QuoteSummary from './QuoteSummary.vue'
import { deliveryText, reasonText } from './display'
import MonitoringStatus from './MonitoringStatus.vue'
import MonitoringHistory from './MonitoringHistory.vue'
import LoginPanel from './LoginPanel.vue'
import HoldingForm from './HoldingForm.vue'
import VersionStatus from './VersionStatus.vue'
import { applyUpdate, applyingUpdate, dismissUpdate, onlineBuild, showUpdate, versionMessage } from './version'
import {
  api,
  badge,
  numberText,
  ruleText,
  setToken,
  sourceName,
  timeText,
  type Holding,
  type Bar,
  type Signal,
  type Run,
  type Health,
  type Settings,
} from './api'
const StockChart = defineAsyncComponent(() => import('./StockChart.vue'))
const signed = ref(false),
  sessionMessage = ref(''),
  error = ref(''),
  note = ref('')
const loading = ref(false),
  checking = ref(false),
  saving = ref(false),
  online = ref(navigator.onLine)
const historyRevision = ref(0)
const holdings = ref<Holding[]>([]),
  signals = ref<Signal[]>([]),
  health = ref<Health | null>(null)
const settings = ref<Settings | null>(null),
  settingsDraft = ref<Settings | null>(null)
const detailBars = ref<Bar[]>([]),
  detailSignals = ref<Signal[]>([]),
  detailBusy = ref(false),
  detailError = ref('')
const formOpen = ref(false),
  editing = ref<Holding | null>(null),
  filter = ref('全部')
const route = ref(location.hash.slice(1) || '/')
const detail = computed(() =>
  route.value.startsWith('/stock/')
    ? holdings.value.find((h) => h.symbol === route.value.slice(7))
    : undefined,
)
const list = computed(() =>
  holdings.value
    .filter((h) => filter.value === '全部' || (filter.value === '启用中' ? h.enabled : !h.enabled))
    .sort((a, b) => {
      const priority = ['alert', 'watch', 'issue', 'ok', 'muted']
      return priority.indexOf(badge(a)[0]) - priority.indexOf(badge(b)[0]) || a.symbol.localeCompare(b.symbol)
    }),
)
const activeHolds = computed(() => holdings.value.filter((h) => h.enabled))
const alerts = computed(
  () => activeHolds.value.filter((h) => ['watch', 'alert'].includes(badge(h)[0])).length,
)
const failures = computed(() => activeHolds.value.filter((h) => badge(h)[0] === 'issue').length)
const closeOnly = computed(() => health.value?.plan?.intraday_validated === false)
let epoch = 0,
  syncVersion = 0,
  detailVersion = 0,
  pollingId = 0
const navItems = [
  { path: '/', text: '持仓总览', icon: 'apps-o' },
  { path: '/signals', text: '信号记录', icon: 'bell' },
  { path: '/settings', text: '设置', icon: 'setting-o' },
]
const runStates: Record<string, string> = {
  RUNNING: '正在检测',
  PENDING: '等待检测',
  QUEUED: '等待检测',
  SUCCESS: '检测完成',
  COMPLETED: '检测完成',
  SUCCEEDED: '检测完成',
  PARTIAL: '部分异常',
  PARTIAL_FAILURE: '部分异常',
  FAILED: '检测失败',
  SKIPPED: '已跳过',
  INTERRUPTED: '已中断',
}
function runLabel(run: Run | null | undefined) {
  return run ? runStates[run.state.toUpperCase()] || run.state : '尚无检测'
}
function go(path: string) {
  location.hash = path
}
function routeChange() {
  route.value = location.hash.slice(1) || '/'
  error.value = ''
  note.value = ''
  window.scrollTo(0, 0)
}
function logout(message = '') {
  signed.value = false
  epoch++
  syncVersion++
  detailVersion++
  pollingId++
  setToken('')
  holdings.value = []
  signals.value = []
  health.value = null
  settings.value = null
  settingsDraft.value = null
  detailBars.value = []
  detailSignals.value = []
  formOpen.value = false
  editing.value = null
  checking.value = false
  loading.value = false
  saving.value = false
  error.value = ''
  note.value = ''
  sessionMessage.value = message
}
function expired() {
  logout('登录已过期，请重新输入密码。')
}
async function sync() {
  const version = ++syncVersion
  loading.value = true
  error.value = ''
  try {
    const [h, s, state, options] = await Promise.all([
      api<Holding[]>('/holdings'),
      api<Signal[]>('/signals?limit=100'),
      api<Health>('/system/status'),
      api<Settings>('/settings'),
    ])
    if (!signed.value || version !== syncVersion) return
    historyRevision.value++
    holdings.value = h
    signals.value = s
    health.value = state
    settings.value = options
    if (!settingsDraft.value) settingsDraft.value = { ...options }
    if (
      state.latest_run &&
      !state.latest_run.finished_at &&
      !checking.value &&
      ['running', 'queued', 'pending'].includes(state.latest_run.state.toLowerCase())
    )
      void monitor(state.latest_run.id)
  } catch (e) {
    if (signed.value && version === syncVersion) error.value = (e as Error).message
  } finally {
    if (version === syncVersion) loading.value = false
  }
}
async function enter() {
  signed.value = true
  sessionMessage.value = ''
  await sync()
}
function edit(h: Holding | null) {
  editing.value = h
  formOpen.value = true
}
async function saved() {
  formOpen.value = false
  note.value = '持仓已保存'
  await sync()
}
async function removeHolding(h: Holding) {
  try {
    await showConfirmDialog({
      title: '删除持仓',
      message: '确认从监控列表移除 ' + h.symbol + '？',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api('/holdings/' + h.id, 'DELETE')
    go('/')
    await sync()
    note.value = '持仓已删除'
  } catch (e) {
    error.value = (e as Error).message
  }
}
async function loadDetail() {
  const h = detail.value,
    version = ++detailVersion
  detailBars.value = []
  detailSignals.value = []
  detailError.value = ''
  if (!h) return
  detailBusy.value = true
  try {
    const [bars, items] = await Promise.all([
      api<Bar[]>('/stocks/' + encodeURIComponent(h.symbol) + '/bars?days=120'),
      api<Signal[]>('/signals?symbol=' + encodeURIComponent(h.symbol) + '&limit=100'),
    ])
    if (version === detailVersion && signed.value) {
      detailBars.value = bars
      detailSignals.value = items
    }
  } catch (e) {
    if (version === detailVersion) detailError.value = (e as Error).message
  } finally {
    if (version === detailVersion) detailBusy.value = false
  }
}
watch(detail, loadDetail)
watch(route, (path) => {
  if (path === '/settings' && settings.value) settingsDraft.value = { ...settings.value }
})
async function monitor(id: number) {
  checking.value = true
  const serial = ++pollingId
  try {
    for (let attempt = 0; attempt < 240 && signed.value && serial === pollingId; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 1500))
      if (!signed.value || serial !== pollingId) return
      const run = await api<Run>('/checks/' + id)
      if (serial !== pollingId) return
      if (health.value) health.value.latest_run = run
      if (run.finished_at || !['RUNNING', 'PENDING', 'QUEUED'].includes(run.state.toUpperCase())) {
        await sync()
        note.value = runLabel(run) + ' · 成功 ' + run.success_count + '，异常 ' + run.error_count
        return
      }
    }
    if (serial === pollingId) note.value = '任务仍在后台运行，请稍后刷新查看结果'
  } catch (e) {
    if (signed.value && serial === pollingId) error.value = (e as Error).message
  } finally {
    if (serial === pollingId) checking.value = false
  }
}
async function check() {
  if (checking.value) return
  const current = epoch
  checking.value = true
  note.value = ''
  error.value = ''
  try {
    const run = await api<{ run_id: number }>('/checks', 'POST')
    if (signed.value && current === epoch) await monitor(run.run_id)
  } catch (e) {
    if (signed.value && current === epoch) {
      checking.value = false
      error.value = (e as Error).message
    }
  }
}
async function saveOptions() {
  if (!settingsDraft.value || saving.value) return
  saving.value = true
  error.value = ''
  note.value = ''
  try {
    const value = await api<Settings>('/settings', 'PUT', settingsDraft.value)
    if (signed.value) {
      settings.value = value
      settingsDraft.value = { ...value }
      await sync()
      note.value = '设置已保存'
    }
  } catch (e) {
    if (signed.value) error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}
function network() {
  online.value = navigator.onLine
}
function fmtEvidence(e: Record<string, unknown>) {
  const labels: Record<string, string> = {
    prev_rsi6: '前日 RSI6',
    prev_rsi12: '前日 RSI12',
    rsi6: '当日 RSI6',
    rsi12: '当日 RSI12',
    curr_rsi6: '当日 RSI6',
    curr_rsi12: '当日 RSI12',
  }
  return Object.entries(e || {})
    .filter(([k, v]) => k in labels && typeof v === 'number')
    .map(([k, v]) => labels[k] + ' ' + numberText(v as number))
    .join(' · ')
}
function signOut() {
  void api('/auth/logout', 'POST').catch(() => {})
  logout()
  go('/')
  nextTick(() => document.getElementById('pwd')?.focus())
}
onMounted(() => {
  window.addEventListener('hashchange', routeChange)
  window.addEventListener('session-expired', expired)
  window.addEventListener('online', network)
  window.addEventListener('offline', network)
})
onBeforeUnmount(() => {
  logout()
  window.removeEventListener('hashchange', routeChange)
  window.removeEventListener('session-expired', expired)
  window.removeEventListener('online', network)
  window.removeEventListener('offline', network)
})
</script>

<template>
  <header class="mast">
    <div class="wrap mastInner">
      <a class="brand" href="#/" aria-label="持仓雷达首页"
        ><img :src="'/stock-radar/icon-192.png'" alt="" />持仓雷达</a
      >
      <nav v-if="signed" class="deskNav">
        <a
          v-for="n in navItems"
          :key="n.path"
          :class="{ current: route === n.path || (n.path === '/' && detail) }"
          :href="'#' + n.path"
          >{{ n.text }}</a
        >
      </nav>
      <button v-if="signed" class="logout" @click="signOut"><van-icon aria-hidden="true" name="revoke" />退出</button>
      <span v-else class="headerTime"><van-icon aria-hidden="true" name="clock-o" />上海时间</span>
    </div>
  </header>
  <main class="wrap" :class="{ signed }">
    <p v-if="!online" class="notice" role="status">当前设备已离线，恢复网络后可继续检测。</p>
    <section v-if="showUpdate" class="notice" aria-label="发现新版本">
      <strong>发现新版本 {{ onlineBuild?.version }}</strong>
      <p v-if="applyingUpdate || versionMessage !== '有新版本可用'" aria-live="polite">
        {{ applyingUpdate ? '正在应用更新…' : versionMessage }}
      </p>
      <p v-if="signed">立即更新会刷新页面，请在更新后重新登录。</p>
      <button class="primary" :disabled="applyingUpdate || !online" @click="applyUpdate">立即更新</button>
      <button class="linkBtn" :disabled="applyingUpdate" @click="dismissUpdate">稍后</button>
    </section>
    <login-panel v-if="!signed" :message="sessionMessage" @logged-in="enter" />
    <template v-else>
      <div v-if="error" class="error strip" role="alert">
        {{ error }} <button class="linkBtn" @click="sync">刷新页面数据</button>
      </div>
      <div v-if="note" class="notice strip" role="status">
        {{ note
        }}<button class="iconBtn" aria-label="关闭提示" @click="note = ''"><van-icon aria-hidden="true" name="cross" /></button>
      </div>
      <template v-if="route === '/'">
        <div class="pageHead">
          <div>
            <h1>持仓总览</h1>
            <p>
              最近检测 · {{ timeText(health?.latest_run?.finished_at || health?.latest_run?.started_at) }}
            </p>
          </div>
          <button class="primary" :disabled="checking || !activeHolds.length || !online" @click="check">
            <van-icon aria-hidden="true" name="replay" :class="{ spin: checking }" />{{ checking ? '正在检测' : closeOnly ? '刷新收盘数据' : '立即检测' }}
          </button>
        </div>
        <p v-if="closeOnly" class="notice" role="status">
          当前仅收盘监控。盘中更新尚待验证，刷新获取最近已收盘的数据。
          <span v-if="health?.scheduler_enabled">交易日 15:45 自动检测。</span>
        </p>
        <div class="summary">
          <div>
            <span><i class="dot blue"></i>监控持仓</span
            ><strong>{{ health ? activeHolds.length : '—' }}</strong>
          </div>
          <div>
            <span><i class="dot amber"></i>关注与预警</span><strong>{{ health ? alerts : '—' }}</strong>
          </div>
          <div>
            <span><i class="dot"></i>数据异常</span><strong>{{ health ? failures : '—' }}</strong>
          </div>
        </div>
        <div class="layout">
          <section>
            <div class="sectionTitle">
              <h2>持仓列表</h2>
              <button class="secondary" @click="edit(null)"><van-icon aria-hidden="true" name="plus" />添加持仓</button>
            </div>
            <div class="seg">
              <button
                v-for="f in ['全部', '启用中', '已停用']"
                :key="f"
                :class="{ chosen: filter === f }"
                @click="filter = f"
              >
                {{ f }}
              </button>
            </div>
            <p v-if="loading" class="mutedText" role="status">正在读取持仓…</p>
            <div v-else-if="!list.length && !error" class="empty panel">
              <div class="emptyIcon"><van-icon aria-hidden="true" name="orders-o" /></div>
              <h3>{{ holdings.length ? '没有符合条件的持仓' : '还没有持仓' }}</h3>
              <p>{{ holdings.length ? '切换筛选查看其他持仓。' : '添加已买入的股票，开始跟踪每日 RSI。' }}</p>
              <button v-if="!holdings.length" class="secondary" @click="edit(null)">添加第一只持仓</button>
            </div>
            <a v-for="h in list" :key="h.id" class="stock panel" :href="'#/stock/' + h.symbol">
              <div class="sectionTitle">
                <div>
                  <h3>{{ h.security_name || h.display_name || h.symbol }}</h3>
                  <span class="ticker">{{ h.symbol }} · {{ h.market }} 股</span>
                </div>
                <span class="tag" :class="badge(h)[0]">{{ badge(h)[1] }}</span>
              </div>
              <QuoteSummary :holding="h" />
              <div class="readings">
                <div v-for="k in ['rsi6', 'rsi12', 'rsi24'] as const" :key="k">
                  <small>{{ k.slice(3) }}日强弱</small
                  ><b>{{ numberText(h.price_context === 'LAST_CLOSE' ? h.close_rsi?.[k] : h[k]) }}</b>
                </div>
              </div>
              <div class="stockFoot">
                <span>{{ h.detected_at ? '检测 ' + timeText(h.detected_at) : sourceName(h.provider) + ' · ' + (h.bar_date || '尚无行情') }}</span
                ><span>详情 <van-icon aria-hidden="true" name="arrow" /></span>
              </div>
            </a>
          </section>
          <aside>
            <div class="panel side">
              <div class="sectionTitle">
                <h2>检测状态</h2>
                <van-icon aria-hidden="true" name="clock-o" />
              </div>
              <div class="line">
                <span>最近任务</span><b>{{ runLabel(health?.latest_run) }}</b>
              </div>
              <p v-if="health?.latest_run?.error_summary" class="warnText">
                {{ reasonText(health.latest_run.error_summary) }}
              </p>
              <div class="line">
                <span>收盘后检测</span
                ><b>{{ health ? (health.scheduler_enabled ? '15:45' : '未启用') : '—' }}</b>
              </div>
              <div class="line">
                <span>微信通知</span
                ><b>{{
                  health
                    ? health.notification_configured && settings?.notifications_enabled
                      ? '已启用'
                      : '未启用'
                    : '—'
                }}</b>
              </div>
              <MonitoringStatus v-if="health" :health="health" />
              <button class="linkBtn" @click="go('/settings')">查看设置 <van-icon aria-hidden="true" name="arrow" /></button>
            </div>
          </aside>
        </div>
      </template>

      <template v-else-if="route.startsWith('/stock/')">
        <a href="#/" class="back"><van-icon aria-hidden="true" name="arrow-left" />持仓总览</a>
        <template v-if="detail">
          <div class="pageHead">
            <div>
              <h1>{{ detail.security_name || detail.display_name || detail.symbol }}</h1>
              <p>{{ detail.symbol }} · {{ detail.market }} 股</p>
            </div>
            <button class="secondary" @click="edit(detail)"><van-icon aria-hidden="true" name="edit" />编辑</button>
          </div>
          <section class="panel side detailTop">
            <span class="tag" :class="badge(detail)[0]">{{ badge(detail)[1] }}</span>
            <QuoteSummary :holding="detail" />
            <p v-if="detail.monitoring_note" class="meta">{{ detail.monitoring_note }}</p>
            <p v-if="detail.reason">{{ reasonText(detail.reason) }}</p>
            <div class="readings">
              <div v-for="k in ['rsi6', 'rsi12', 'rsi24'] as const" :key="k">
                <small>{{ k.slice(3) }}日强弱</small
                ><b>{{ numberText(detail.price_context === 'LAST_CLOSE' ? detail.close_rsi?.[k] : detail[k]) }}</b>
              </div>
            </div>
            <p class="meta">
              行情日期 {{ detail.bar_date || '—' }} · {{ sourceName(detail.provider) }} · 取数
              {{ timeText(detail.fetched_at) }}
            </p>
            <p v-if="detail.price_context === 'INTRADAY'" class="meta">盘中 RSI 为暂定值 · 检测 {{ timeText(detail.detected_at) }} · 上一正式收盘 RSI：{{ numberText(detail.close_rsi?.rsi6) }} / {{ numberText(detail.close_rsi?.rsi12) }} / {{ numberText(detail.close_rsi?.rsi24) }}</p>
            <p v-if="detail.notes">备注 · {{ detail.notes }}</p>
          </section>
          <p v-if="detailError" class="error" role="alert">
            {{ detailError }} <button class="linkBtn" @click="loadDetail">重试</button>
          </p>
          <p v-else-if="detailBusy">正在读取日线…</p>
          <template v-else-if="detailBars.length">
            <p v-if="detail.price_context === 'INTRADAY'" class="meta">下图使用正式收盘日线。最新盘中指标见上方。</p>
            <stock-chart :bars="detailBars" />
          </template>
          <p v-else class="panel side mutedText">尚无已校验日线，可在总览发起检测。</p>
          <section class="panel side">
            <h2>信号历史</h2>
            <p v-if="!detailSignals.length && !detailBusy && !detailError" class="mutedText">暂无信号记录</p>
            <div
              v-for="s in detailSignals"
              :key="s.bar_date + s.rule_code + s.strategy_version"
              class="historyRow"
            >
              <strong>{{ ruleText(s.rule_code) }}</strong>
              <small>{{ s.bar_date }} · {{ s.strategy_version }}</small>
              <p>{{ fmtEvidence(s.evidence) }}</p>
            </div>
          </section>
          <button class="quietBtn" @click="removeHolding(detail)">删除此持仓</button>
        </template>
        <p v-else-if="!loading && !error" class="notice">该持仓已移除或不存在。</p>
      </template>

      <template v-else-if="route === '/signals'">
        <div class="pageHead">
          <div>
            <h1>信号记录</h1>
            <p>查看收盘信号、盘中变化和微信简报</p>
          </div>
          <button class="secondary" @click="sync">刷新</button>
        </div>
        <MonitoringHistory :signals="signals" :revision="historyRevision" />
      </template>

      <template v-else-if="route === '/settings'">
        <div class="pageHead">
          <div>
            <h1>设置</h1>
            <p>管理检测时段、风险阈值与推送额度</p>
          </div>
        </div>
        <div class="layout" v-if="settingsDraft && health">
          <form class="panel side options" @submit.prevent="saveOptions">
            <h2>风险规则</h2>
            <label
              >超买突破阈值<input
                v-model.number="settingsDraft.overbought"
                type="number"
                min="1"
                max="100"
                step="0.1"
                required
            /></label>
            <p>6 日相对强弱指标（RSI）从阈值或以下升至阈值以上，产生关注信号。</p>
            <label
              >高位死叉门槛<input
                v-model.number="settingsDraft.high_level"
                type="number"
                min="0"
                max="100"
                step="0.1"
                required
            /></label>
            <p>前一交易日的 6 日强弱指标达到门槛，当天从上向下穿过 12 日指标，产生重点预警。</p>
            <div class="line">
              <span>微信通知总开关</span
              ><van-switch
                v-model="settingsDraft.notifications_enabled"
                size="24px"
                aria-label="微信通知总开关"
              />
            </div>
            <p v-if="!health.notification_configured">请让管理员配置微信推送密钥。</p>
            <template v-if="health.version">
              <h2>盘中监控</h2>
              <div class="line"><span>自动盘中检测</span><van-switch v-model="settingsDraft.intraday_enabled" :disabled="!health.plan?.intraday_validated" size="24px" aria-label="自动盘中检测" /></div>
              <p v-if="!health.plan?.intraday_validated">等待交易时段行情与复权核验。此期间简报逐只列出最近有效收盘及日期。</p>
              <label>检测间隔<select v-model.number="settingsDraft.interval_minutes"><option v-for="m in [15,30,60]" :key="m" :value="m">{{ m }} 分钟</option></select></label>
              <label>每个行情源每日请求上限<input v-model.number="settingsDraft.api_daily_limit" type="number" min="1" max="10000" required /></label>
              <h2>简报与即时提醒</h2>
              <div class="line"><span>交易日持仓简报</span><van-switch v-model="settingsDraft.brief_enabled" size="24px" aria-label="交易日持仓简报" /></div>
              <label>简报时间（上海）<input v-model="settingsDraft.brief_time" type="time" min="09:30" max="14:59" required /></label>
              <div class="line"><span>重点风险即时提醒</span><van-switch v-model="settingsDraft.urgent_enabled" size="24px" aria-label="重点风险即时提醒" /></div>
              <div class="line"><span>普通超买关注提醒</span><van-switch v-model="settingsDraft.watch_enabled" size="24px" aria-label="普通超买关注提醒" /></div>
              <label>每日微信额度<input v-model.number="settingsDraft.message_daily_limit" type="number" min="1" max="10000" required /></label>
            </template>
            <button class="primary" :disabled="saving">{{ saving ? '正在保存…' : '保存设置' }}</button>
          </form>
          <aside class="panel side">
            <h2>服务状态</h2>
            <div class="line">
              <span>同花顺凭据</span><b>{{ health.hithink_configured ? '已配置' : '待配置' }}</b>
            </div>
            <div class="line">
              <span>微信推送密钥</span><b>{{ health.notification_configured ? '已配置' : '待配置' }}</b>
            </div>
            <div class="line">
              <span>定时检测</span><b>{{ health.scheduler_enabled ? '已启用' : '未启用' }}</b>
            </div>
            <p class="meta">交易日 15:45，上海时间</p>
            <div class="line">
              <span>最近推送</span><b>{{ deliveryText(health.latest_delivery?.status) }}</b>
            </div>
            <p v-if="health.latest_delivery" class="meta">
              {{ timeText(health.latest_delivery.created_at) }} {{ reasonText(health.latest_delivery.reason) }}
            </p>
          </aside>
        </div>
        <VersionStatus :backend="health?.version" />
      </template>
      <p v-else class="notice">此页面不存在。<a href="#/">返回持仓总览</a></p>
      <holding-form :show="formOpen" :holding="editing" @close="formOpen = false" @saved="saved" />
    </template>
  </main>
  <nav v-if="signed" class="mobileNav">
    <a
      v-for="n in navItems"
      :key="n.path"
      :href="'#' + n.path"
      :class="{ current: route === n.path || (n.path === '/' && detail) }"
    >
      <van-icon aria-hidden="true" :name="n.icon" />{{ n.text }}</a
    >
  </nav>
</template>
