import { computed, ref } from 'vue'

interface Build {
  version: string
  commit: string
  builtAt: string
}
declare const __STOCK_RADAR_BUILD__: Build

export const currentBuild = __STOCK_RADAR_BUILD__
export const onlineBuild = ref<Build | null>(null)
export const checkingVersion = ref(false)
export const applyingUpdate = ref(false)
export const versionMessage = ref('')
const dismissed = ref('')
const identity = (build: Build) => [build.version, build.commit, build.builtAt].join('/')
export const hasUpdate = computed(() => !!onlineBuild.value && identity(onlineBuild.value) !== identity(currentBuild))
export const showUpdate = computed(() => !!onlineBuild.value && hasUpdate.value && dismissed.value !== identity(onlineBuild.value))
let registration: Promise<ServiceWorkerRegistration | null> | undefined
let startupController: ServiceWorker | null = null

export function dismissUpdate() {
  if (onlineBuild.value) dismissed.value = identity(onlineBuild.value)
}

function getRegistration() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return Promise.resolve(null)
  if (!registration) {
    const base = import.meta.env.BASE_URL
    registration = navigator.serviceWorker.register(base + 'sw.js', { scope: base, updateViaCache: 'none' })
      .catch(() => { registration = undefined; return null })
  }
  return registration
}

export async function checkVersion(manual = false) {
  if (manual) dismissed.value = ''
  if (checkingVersion.value || applyingUpdate.value) return
  checkingVersion.value = true
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 10000)
  try {
    const response = await fetch(import.meta.env.BASE_URL + 'version.json?t=' + Date.now(), {
      cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal,
    })
    if (!response.ok) throw new Error()
    const build = await response.json()
    if (!build || !['version', 'commit', 'builtAt'].every(key => typeof build[key] === 'string' && build[key]) ||
        !Number.isFinite(Date.parse(build.builtAt))) throw new Error()
    onlineBuild.value = build
    versionMessage.value = hasUpdate.value ? '有新版本可用' : '已是最新版本'
    if (hasUpdate.value) void getRegistration().then(reg => reg?.update()).catch(() => {})
  } catch {
    versionMessage.value = '暂时无法检查更新，请在联网后重试。'
  } finally {
    window.clearTimeout(timer)
    checkingVersion.value = false
  }
}

function installed(worker: ServiceWorker) {
  return new Promise<void>((resolve, reject) => {
    const finish = () => {
      window.clearTimeout(timer)
      worker.removeEventListener('statechange', change)
    }
    const change = () => {
      if (worker.state === 'redundant') { finish(); reject(new Error()); return }
      if (['installed', 'activating', 'activated'].includes(worker.state)) { finish(); resolve() }
    }
    const timer = window.setTimeout(() => { finish(); reject(new Error()) }, 30000)
    worker.addEventListener('statechange', change)
    change()
  })
}

// Reload only the tab whose user explicitly requested installation. Other tabs
// keep their in-memory session even when the new worker takes control of them.
async function activate(reg: ServiceWorkerRegistration) {
  await reg.update()
  if (reg.installing) await installed(reg.installing)
  const worker = reg.waiting
  if (!worker) {
    if (navigator.serviceWorker.controller === startupController && startupController) throw new Error()
    return
  }
  await new Promise<void>((resolve, reject) => {
    const finish = () => {
      window.clearTimeout(timer)
      navigator.serviceWorker.removeEventListener('controllerchange', changed)
    }
    const changed = () => { finish(); resolve() }
    const timer = window.setTimeout(() => { finish(); reject(new Error()) }, 15000)
    navigator.serviceWorker.addEventListener('controllerchange', changed)
    worker.postMessage({ type: 'SKIP_WAITING' })
  })
}

export async function applyUpdate() {
  if (!onlineBuild.value || !hasUpdate.value || applyingUpdate.value) return
  applyingUpdate.value = true
  try {
    const reg = await getRegistration()
    if (reg) {
      await activate(reg)
      location.reload()
      return
    }
    if (startupController) throw new Error()
    const url = new URL(location.href)
    url.searchParams.set('v', onlineBuild.value.commit + '.' + Date.parse(onlineBuild.value.builtAt))
    if (url.href === location.href) location.reload()
    else location.replace(url.href)
  } catch {
    versionMessage.value = '新版文件尚未就绪，请稍后再检查。'
    applyingUpdate.value = false
  }
}

export function startVersionChecks() {
  startupController = navigator.serviceWorker?.controller ?? null
  void getRegistration()
  void checkVersion()
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void checkVersion()
  })
  window.addEventListener('online', () => void checkVersion())
}
