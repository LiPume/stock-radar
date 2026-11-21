// Public release smoke test. Uses no production password, holding or API key.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { verifyChart } from './chart-fixture.mjs'

const pages = new URL(process.env.PAGES_URL || 'https://lipume.github.io/stock-radar/')
const api = new URL(process.env.API_BASE_URL || 'https://8.217.211.34/api/v1')
assert.ok(pages.pathname.endsWith('/'), 'PAGES_URL must end in /')
const {version} = JSON.parse(await readFile(new URL('../package.json', import.meta.url)))
const expected = process.env.EXPECTED_COMMIT || process.env.GITHUB_SHA
async function verifyRelease(context) {
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  // Check release marker before loading a bundle during CDN propagation.
  const response = await context.request.get(new URL('version.json',pages).href,{
    timeout:10000, failOnStatusCode:false, headers:{'Cache-Control':'no-cache'} })
  const marker = response.ok() ? await response.json().catch(()=>null) : null
  assert.equal(marker?.version,version,'Pages version.json does not match the package version')
  assert.match(marker.commit,/^[0-9a-f]{40}$/)
  if (expected) assert.equal(marker.commit,expected,'Pages version.json belongs to another commit')
  assert.ok(Number.isFinite(Date.parse(marker.builtAt)) && marker.builtAt.endsWith('Z'),'invalid build time')
  const index = await page.goto(pages.href,{waitUntil:'domcontentloaded'})
  assert.equal(index.status(),200)
  await page.getByLabel('管理员密码',{exact:true}).waitFor()
  const files = await page.locator('script[src],link[rel="stylesheet"],link[rel="manifest"]').evaluateAll(
    list => list.map(el=>el.src || el.href))
  assert.ok(files.some(f=>new URL(f).pathname.endsWith('.js')))
  assert.ok(files.some(f=>new URL(f).pathname.endsWith('.css')))
  assert.ok(files.some(f=>new URL(f).pathname.endsWith('.webmanifest')))
  for(const href of [...files,new URL('sw.js',pages).href]) {
    const u = new URL(href)
    assert.equal(u.origin,pages.origin)
    assert.ok(u.pathname.startsWith(pages.pathname))
    const r = await context.request.get(href)
    assert.equal(r.status(),200,'asset: ' + u.pathname)
    assert.doesNotMatch(r.headers()['content-type'] || '',/text\/html/i,'asset returned index HTML: ' + u.pathname)
  }
  const dist = process.env.VERIFY_DIST_DIR || (process.env.GITHUB_ACTIONS === 'true' ? fileURLToPath(new URL('../dist', import.meta.url)) : '')
  if (dist) {
    const relative = ['index.html','version.json','sw.js',...(await readdir(join(dist,'assets'))).map(f=>'assets/'+f)]
    for (const f of relative) {
      const r = await context.request.get(new URL(f==='index.html' ? '' : f,pages).href)
      assert.ok(r.ok() && (await r.body()).equals(await readFile(join(dist,f))),'published bytes differ from deployment artifact: '+f)
    }
  }
  // Real, cross-origin browser GET requests. Authentication and portfolio tests below are isolated.
  for(const [path,status] of [['health',200],['holdings',401],['settings',401],['history',401]]) {
    const result = await page.evaluate(async url=>{
      const r=await fetch(url,{credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)})
      const json=await r.json()
      return {status:r.status,health:json.data?.status,privateData:json.data!=null}
    }, api.href.replace(/\/$/,'')+'/'+path)
    assert.equal(result.status,status,path)
    if (status===200) assert.equal(result.health,'ok')
    else assert.equal(result.privateData,false,path+' exposed anonymous data')
  }
  await verifyChart(page,api.href)
  await page.locator('a[href="#/settings"]:visible').last().click()
  const versions = page.getByRole('region',{name:'应用版本'})
  await versions.getByRole('button',{name:'检查更新'}).click()
  for (const label of ['当前版本','线上版本']) {
    const row = versions.locator('.line').filter({hasText:label})
    await row.getByText(marker.version + ' · ' + marker.commit.slice(0,8),{exact:true}).waitFor()
  }
  assert.deepEqual(errors,[])
  console.log('Pages release '+marker.version+' '+marker.commit+' verified. Static assets, CORS, health, anonymous 401s and six mobile chart ranges passed.')
}

const browser = await chromium.launch({headless:true})
try {
  for (let attempt = 1; attempt <= 4; attempt++) {
    const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,
      locale:'zh-CN',timezoneId:'Asia/Shanghai',serviceWorkers:'block'})
    try {
      await verifyRelease(context)
      break
    } catch (err) {
      // Keep normal URLs as the acceptance criterion. A distinct marker URL
      // separates an old CDN object from a missing publish without masking it.
      const bust = new URL('version.json',pages)
      bust.searchParams.set('release_diagnostic',String(Date.now()))
      try {
        const response = await context.request.get(bust.href,{timeout:8000})
        const diagnostic = await response.json().catch(()=>null)
        console.warn('Attempt '+attempt+' failed. Uncached version marker: HTTP '+response.status()+
          ', commit '+(typeof diagnostic?.commit==='string' && /^[0-9a-f]{40}$/.test(diagnostic.commit) ? diagnostic.commit : 'unavailable'))
      } catch {
        console.warn('Attempt '+attempt+' failed; version diagnostic unavailable.')
      }
      if (attempt===4) throw err
    } finally {
      await context.close()
    }
    await new Promise(resolve=>setTimeout(resolve,15000))
  }
} finally {
  await browser.close()
}
