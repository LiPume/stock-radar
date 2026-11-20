<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { Popup as VanPopup, Icon as VanIcon, Switch as VanSwitch } from 'vant'
import { api, type Holding } from './api'
const props = defineProps<{ show: boolean; holding: Holding | null }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const form = reactive({ code: '', exchange: 'SH', market: 'A', display_name: '', notes: '', enabled: true })
const busy = ref(false),
  error = ref('')
watch(
  () => props.show,
  (open) => {
    if (!open) return
    const h = props.holding
    Object.assign(form, {
      code: h?.symbol.split('.')[0] ?? '',
      exchange: h?.symbol.split('.')[1] ?? 'SH',
      market: h?.market ?? 'A',
      display_name: h?.display_name ?? '',
      notes: h?.notes ?? '',
      enabled: h?.enabled ?? true,
    })
    error.value = ''
  },
)
function infer() {
  const code = form.code
  if (!/^\d{6}$/.test(code)) return
  form.exchange = /^(6|9)/.test(code) ? 'SH' : /^(4|8)/.test(code) ? 'BJ' : 'SZ'
  if (code.startsWith('92')) form.exchange = 'BJ'
  form.market = /^(900|200)/.test(code) ? 'B' : 'A'
}
async function save() {
  if (busy.value) return
  const h = props.holding,
    symbol = form.code + '.' + form.exchange
  busy.value = true
  error.value = ''
  try {
    if (!h && !/^\d{6}$/.test(form.code)) throw Error('请输入六位股票代码')
    const fields = { display_name: form.display_name.trim(), notes: form.notes.trim() }
    if (h) await api('/holdings/' + h.id, 'PATCH', { ...fields, enabled: form.enabled })
    else await api('/holdings', 'POST', { symbol, market: form.market, ...fields })
    emit('saved')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <van-popup
    :show="show"
    position="bottom"
    round
    safe-area-inset-bottom
    :close-on-click-overlay="!busy"
    :style="{ maxHeight: '92dvh' }"
    @click-overlay="!busy && emit('close')"
  >
    <form class="sheet" @submit.prevent="save">
      <div class="sectionTitle">
        <h2>{{ holding ? '编辑持仓' : '添加持仓' }}</h2>
        <button type="button" class="iconBtn" aria-label="关闭" :disabled="busy" @click="emit('close')">
          <van-icon aria-hidden="true" name="cross" />
        </button>
      </div>
      <label
        >股票代码<input
          v-model.trim="form.code"
          required
          pattern="[0-9]{6}"
          maxlength="6"
          inputmode="numeric"
          placeholder="输入六位代码"
          :disabled="!!holding"
          @input="infer"
      /></label>
      <div class="formRow">
        <label
          >交易所<select v-model="form.exchange" :disabled="!!holding">
            <option value="SH">上海</option>
            <option value="SZ">深圳</option>
            <option value="BJ">北京</option>
          </select></label
        >
        <label
          >市场<select v-model="form.market" :disabled="!!holding">
            <option value="A">A 股</option>
            <option value="B">B 股</option>
          </select></label
        >
      </div>
      <label
        >股票名称 <small>选填</small
        ><input v-model="form.display_name" maxlength="50" placeholder="便于识别的名称"
      /></label>
      <label
        >备注 <small>选填</small><textarea v-model="form.notes" maxlength="300" rows="2"></textarea>
      </label>
      <div v-if="holding" class="line">
        <span>启用监控</span><van-switch v-model="form.enabled" size="24px" aria-label="启用监控" />
      </div>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <button class="primary wide" :disabled="busy">{{ busy ? '保存中…' : '保存持仓' }}</button>
    </form>
  </van-popup>
</template>
