export interface Holding {
  security_name?: string | null
  price?: number | null
  currency?: string
  change_pct?: number | null
  price_context?: 'CLOSE' | 'INTRADAY' | 'LAST_CLOSE'
  quote_time?: string | null
  close_rsi?: Record<'rsi6' | 'rsi12' | 'rsi24', number | null>
  id: number
  symbol: string
  market: 'A' | 'B'
  display_name: string
  notes: string
  enabled: boolean
  data_status: string
  status: string
  reason: string | null
  provider: string | null
  bar_date: string | null
  fetched_at: string | null
  rsi6: number | null
  rsi12: number | null
  rsi24: number | null
}
export interface Bar {
  bar_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  provider: string
  adjust: string
  fetched_at: string
  rsi6: number | null
  rsi12: number | null
  rsi24: number | null
}
export interface Signal {
  holding_id: number
  security_name?: string | null
  symbol: string
  bar_date: string
  rule_code: string
  severity: string
  strategy_version: string
  evidence: Record<string, unknown>
  created_at: string
}
export interface Run {
  id: number
  trigger_type: string
  state: string
  started_at: string
  finished_at: string | null
  success_count: number
  error_count: number
  error_summary: string | null
}
export interface Settings {
  overbought: number
  high_level: number
  notifications_enabled: boolean
  intraday_enabled: boolean
  interval_minutes: number
  brief_enabled: boolean
  brief_time: string
  urgent_enabled: boolean
  watch_enabled: boolean
  message_daily_limit: number
  api_daily_limit: number
}
export interface SourceUsage {
  today: { http_requests: number; successes: number; failures: number; cache_hits: number; last_success: string | null }
  month: { http_requests: number }
  pause_reason: string | null
}
export interface Health {
  version?: string
  plan?: { planned_today: number; next_scan_at: string | null; next_brief_at: string | null; intraday_validated: boolean }
  runs_today?: { actual: number; success: number; failed: number; partial: number; running: number }
  sources?: Record<string, SourceUsage>
  messages?: { used: number; remaining: number; limit: number; brief_status: string }
  latest_run: Run | null
  hithink_configured: boolean
  notification_configured: boolean
  scheduler_enabled: boolean
  latest_delivery: { status: string; reason: string | null; created_at: string } | null
}

function configuredRoot(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL || '').trim()
  if (!raw) {
    return import.meta.env.DEV || ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)
      ? '/api/v1'
      : ''
  }
  try {
    const url = new URL(raw)
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) return ''
    return url.href.replace(/\/+$/, '')
  } catch {
    return ''
  }
}
export const apiRoot = configuredRoot()
let accessToken = ''
export function setToken(token: string) {
  accessToken = token
}
export class ApiError extends Error {
  constructor(
    public status: number,
    msg: string,
  ) {
    super(msg)
  }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  if (!apiRoot) throw new ApiError(0, '管理者尚未连接 HTTPS 后端')
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 18000)
  try {
    const headers: Record<string, string> = {}
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (accessToken) headers.Authorization = 'Bearer ' + accessToken
    const response = await fetch(apiRoot + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      signal: controller.signal,
    })
    const envelope = await response.json().catch(() => null)
    if (!response.ok) {
      if (response.status === 401 && accessToken) {
        setToken('')
        window.dispatchEvent(new Event('session-expired'))
      }
      const fallback = response.status === 429 ? '操作过于频繁，请稍后再试' : '请求失败'
      throw new ApiError(response.status, envelope?.error?.message || fallback)
    }
    if (!envelope || !Object.hasOwn(envelope, 'data')) throw new ApiError(0, '后端响应格式异常')
    return envelope.data as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(0, '未能连接后端，请检查网络后重试')
  } finally {
    window.clearTimeout(timer)
  }
}

export const numberText = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(2)
export function timeText(value: string | null | undefined) {
  if (!value) return '尚无记录'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
export function sourceName(provider: string | null) {
  return provider === 'hithink' ? '同花顺' : provider === 'akshare-b' ? 'AKShare · 新浪' : provider || '—'
}
export function ruleText(code: string) {
  return (
    (
      { RSI_OVERBOUGHT_CROSS: 'RSI6 上破超买阈值', RSI_HIGH_DEATH_CROSS: 'RSI6 高位下穿 RSI12' } as Record<
        string,
        string
      >
    )[code] || code
  )
}
const validData = ['OK', 'PASS', 'FRESH', 'VALID', 'SUCCESS', 'READY']
export function badge(h: Holding): [string, string] {
  if (!h.enabled) return ['muted', '已停用']
  const data = h.data_status.toUpperCase()
  if (['PENDING', 'NOT_CHECKED', 'UNCHECKED', 'NEVER_CHECKED'].includes(data)) return ['muted', '待检测']
  if (!validData.includes(data)) return ['issue', data === 'STALE' ? '行情滞后' : '数据异常']
  if (h.status === 'ALERT') return ['alert', '重点预警']
  if (h.status === 'WATCH') return ['watch', '超买关注']
  return ['ok', '未触发规则']
}
