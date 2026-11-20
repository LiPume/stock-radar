<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Icon as VanIcon } from 'vant'
import { api, apiRoot, setToken } from './api'
const emit = defineEmits<{ loggedIn: [] }>()
defineProps<{ message: string }>()
const password = ref(''),
  reveal = ref(false),
  busy = ref(false),
  error = ref('')
const connection = ref<'loading' | 'online' | 'offline'>(apiRoot ? 'loading' : 'offline')
async function connect() {
  connection.value = 'loading'
  try {
    await api('/health')
    connection.value = 'online'
  } catch {
    connection.value = 'offline'
  }
}
async function login() {
  if (!password.value || busy.value || !apiRoot) return
  busy.value = true
  error.value = ''
  try {
    const data = await api<{ access_token: string }>('/auth/login', 'POST', { password: password.value })
    if (!data.access_token) throw Error('登录响应异常，请检查服务配置')
    setToken(data.access_token)
    password.value = ''
    emit('loggedIn')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
onMounted(() => {
  if (apiRoot) void connect()
})
</script>
<template>
  <div class="loginLayout">
    <aside class="instrument">
      <div class="instrumentHead"><span class="lightDot"></span>RSI 日线监控</div>
      <div class="scope" aria-hidden="true">
        <div class="sweep"></div>
        <div class="axisX"></div>
        <div class="axisY"></div>
        <i></i><b></b>
      </div>
      <div class="instrumentFoot">
        <span class="big">持仓雷达</span>
        <p>跟踪收盘行情，查看持仓的 RSI 风险信号。</p>
      </div>
    </aside>
    <section class="loginPanel">
      <div class="loginIcon"><van-icon aria-hidden="true" name="manager-o" /></div>
      <h1>登录工作台</h1>
      <p class="lead">使用管理员密码查看和管理你的持仓。</p>
      <p v-if="message" role="status" class="notice">{{ message }}</p>
      <form @submit.prevent="login">
        <label for="pwd">管理员密码</label>
        <div class="passwrap">
          <input
            id="pwd"
            v-model="password"
            :type="reveal ? 'text' : 'password'"
            required
            :disabled="!apiRoot"
            autocomplete="current-password"
            placeholder="请输入密码"
          />
          <button
            type="button"
            :aria-label="reveal ? '隐藏密码' : '显示密码'"
            :aria-pressed="reveal"
            @click="reveal = !reveal"
          >
            <van-icon aria-hidden="true" :name="reveal ? 'closed-eye' : 'eye-o'" />
          </button>
        </div>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button class="primary wide" :disabled="busy || !apiRoot">
          {{ busy ? '正在登录…' : '登录' }} <van-icon aria-hidden="true" name="arrow" />
        </button>
      </form>
      <div class="connection">
        <i class="dot" :class="{ good: connection === 'online' }"></i>
        {{
          !apiRoot
            ? '尚未配置线上后端'
            : connection === 'loading'
              ? '正在连接服务'
              : connection === 'online'
                ? '后端已连接'
                : '后端未连接'
        }}
        <button v-if="apiRoot && connection === 'offline'" class="linkBtn" @click="connect">重试</button>
      </div>
      <p v-if="!apiRoot" class="configMsg">
        请在仓库变量中设置 HTTPS 后端地址 VITE_API_BASE_URL，并重新发布网页。
      </p>
    </section>
  </div>
</template>
