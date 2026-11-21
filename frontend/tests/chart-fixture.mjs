// A fixed synthetic portfolio. Nothing from these fixtures is sent to a backend.
import assert from 'node:assert/strict'

const bars = []
for (let day = Date.UTC(2025, 5, 2); bars.length < 120; day += 86400000) {
  const t = new Date(day)
  if (t.getUTCDay() === 0 || t.getUTCDay() === 6) continue
  const n = bars.length / 100
  bars.push({ bar_date: t.toISOString().slice(0, 10), open: 10 + n, high: 15 + n,
    low: 9 + n, close: 12 + n, ma5: 12.9 + n, ma10: 12.3 + n, ma20: 11.5 + n,
    rsi6: 40 + n, rsi12: 50 + n, rsi24: 54 + n, volume: 1000,
    adjust: 'forward', provider: 'hithink', fetched_at: t.toISOString() })
}
const holdings = Array.from({length:7},(_,index)=>({
  id: index+1, symbol: `TST${String(index+1).padStart(3,'0')}.SH`, market: 'A', enabled: true, currency: 'CNY',
  display_name: '别名' + (index+1), security_name: '合成证券' + (index+1),
  quote_time: bars.at(-1).bar_date, price_context: 'CLOSE',
  status: 'NORMAL', data_status: 'OK', price: bars.at(-1).close, change_pct: 2.434,
  ...bars.at(-1) }))
const holding = holdings[0]
const expectedFields = [['收盘价','close'], ['5日线','ma5'], ['10日线','ma10'], ['20日线','ma20'],
  ['6日','rsi6'], ['12日','rsi12'], ['24日','rsi24']]

export async function verifyChart(page, base) {
  const root = new URL(base)
  const fixture = {
    'auth/login': { access_token: 'release-verification-fixture' },
    health: {status:'ok'}, holdings, signals: [],
    'system/status': { latest_run: null, sources: {}, scheduler_enabled: false },
    settings: { high_level:70, overbought:80, interval_minutes:30, brief_time:'14:30', api_daily_limit:30, message_daily_limit:5 },
    ['stocks/' + holding.symbol + '/bars']: bars,
  }
  const unexpected = []
  // Intercept before typing: no login or private request reaches the real API.
  const app = new URL(page.url())
  await page.route('**/*', async (route) => {
    const u = new URL(route.request().url())
    if (u.origin === app.origin && u.pathname.startsWith(app.pathname) && route.request().method() === 'GET') {
      return route.fallback()
    }
    const prefix = root.pathname.replace(/\/$/,'') + '/'
    const key = u.pathname.startsWith(prefix) ? u.pathname.slice(prefix.length) : ''
    const headers = { 'Access-Control-Allow-Origin': new URL(page.url()).origin,
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Authorization, Content-Type' }
    if (u.origin !== root.origin || !Object.hasOwn(fixture, key)) {
      unexpected.push(u.origin + u.pathname)
      return route.abort()
    }
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers })
    return route.fulfill({ json: { data: fixture[key] }, headers })
  })
  // Rich-text tooltips use Canvas. Observe what ECharts actually paints.
  await page.evaluate(() => {
    const paint = CanvasRenderingContext2D.prototype.fillText
    window.chartLabels = []
    CanvasRenderingContext2D.prototype.fillText = function (t, ...args) {
      window.chartLabels.push(String(t))
      return paint.call(this, t, ...args)
    }
  })
  await page.getByLabel('管理员密码', { exact: true }).fill('fixture-only')
  await page.getByRole('button', { name:'登录', exact:true }).click()
  for (const sample of holdings) {
    const title = page.locator('a.stock[href="#/stock/' + sample.symbol + '"] h3')
    await title.waitFor()
    assert.equal(await title.innerText(),sample.security_name)
  }
  assert.equal(await page.locator('a.stock').count(),holdings.length)
  const card = page.locator('a.stock').filter({hasText:holding.security_name})
  await card.waitFor()
  assert.deepEqual(await card.locator('.quoteMeans b').allTextContents(), ['ma5','ma10','ma20'].map(k=>holding[k].toFixed(2)))
  await card.click()
  await page.getByRole('heading',{level:1,name:holding.security_name,exact:true}).waitFor()
  const chart = page.locator('.chart')
  const ranges = chart.getByRole('group', {name:'图表区间（交易日）'})
  const range = n => ranges.getByRole('button', {name:'近' + n + '日', exact:true})
  await range(20).waitFor()
  assert.equal(await range(20).getAttribute('aria-pressed'), 'true')
  for (const n of [5,10,20,30,60,120]) {
    await range(n).click()
    assert.equal(await range(n).getAttribute('aria-pressed'), 'true')
    const plot = chart.getByRole('img', {name:'最近 ' + n + ' 个交易日的收盘蜡烛图、均线与相对强弱指标'})
    await plot.scrollIntoViewIfNeeded()
    const bounds = await plot.boundingBox()
    const indices = n <= 20 ? [...Array(n).keys()] : [0, Math.floor(n/2), n - 1]
    for (const i of indices) {
      const row = bars[bars.length - n + i]
      // Each trade date occupies one category band between the chart grid margins.
      const x = bounds.x + 53 + (bounds.width - 63) * (i + 0.5) / n
      const y = bounds.y + 50
      await page.mouse.move(0,y)
      await page.evaluate(() => { window.chartLabels.length = 0 })
      await page.mouse.move(x,y)
      await page.waitForFunction(date=>window.chartLabels.includes(date),row.bar_date,{timeout:5000})
      const labels = await page.evaluate(()=>window.chartLabels.join(' '))
      for (const [label,key] of expectedFields) {
        assert.ok(labels.includes(label + ' ' + row[key].toFixed(2)), label + ' does not match ' + row.bar_date)
      }
    }
  }
  const ma = chart.getByRole('group',{name:'切换收盘均线'}).getByRole('button',{name:'5日线',exact:true})
  await ma.click()
  assert.equal(await ma.getAttribute('aria-pressed'), 'false')
  assert.equal(await range(120).getAttribute('aria-pressed'), 'true')
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), 'mobile page overflows')
  assert.deepEqual(unexpected, [])
}
