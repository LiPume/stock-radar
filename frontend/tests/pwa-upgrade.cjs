// Chromium exercises the legacy and current workers on one persistent localhost origin.
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const { createHash } = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const { parseArgs } = require('node:util')
const { chromium } = require('playwright')

const frontend = path.resolve(__dirname, '..')
const repo = path.dirname(frontend)
const scope = '/stock-radar/'
const args = parseArgs({ options: {
  'old-ref': { type: 'string', default: '1b289b27fdbcc6fdd237ee82aa9f0c9c72e15da9' },
  dist: { type: 'string', default: 'frontend/dist' }, 'published-url': { type: 'string' },
} }).values
const release = path.resolve(args.dist)
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const marker = dir => JSON.parse(fs.readFileSync(path.join(dir, 'version.json'), 'utf8'))
function mainScript(dir) {
  const html = fs.readFileSync(path.join(dir,'index.html'),'utf8')
  const match = html.match(/<script[^>]*\btype="module"[^>]*\bsrc="\/stock-radar\/(assets\/[\w.-]+\.js)"/)
  assert.ok(match, 'expected module script in the published index')
  return match[1]
}

async function checkPublished(url) {
  // Use the archived deployment artifact, including the build time, as the reference.
  const files = ['version.json','index.html','sw.js',mainScript(release)]
  const origin = new URL(url)
  assert.equal(origin.protocol, 'https:')
  assert.ok(origin.pathname.endsWith('/'))
  for (let attempt=0; attempt<12; attempt++) {
    try {
      for (const name of files) {
        const target = new URL(name,origin)
        target.searchParams.set('verify_release', String(Date.now()))
        const r = await fetch(target, { cache: 'no-store', redirect:'error', signal:AbortSignal.timeout(10000) })
        assert.equal(r.status,200,name)
        assert.equal(sha(Buffer.from(await r.arrayBuffer())),sha(fs.readFileSync(path.join(release,name))),name)
      }
      return
    } catch (error) {
      if (attempt===11) throw error
      await new Promise(resolve=>setTimeout(resolve,5000))
    }
  }
}

function builds(tmp) {
  assert.match(args['old-ref'],/^[a-f\d]{7,40}$/)
  const archive = execFileSync('git',['archive',args['old-ref'],'frontend'],{cwd:repo})
  execFileSync('tar',['-xf','-','-C',tmp],{input:archive})
  const old = path.join(tmp,'frontend')
  fs.symlinkSync(path.join(frontend,'node_modules'),path.join(old,'node_modules'),'dir')
  const vite = path.join(frontend,'node_modules','vite','bin','vite.js')
  const env = {...process.env, VITE_API_BASE_URL:process.env.API_BASE_URL || 'https://8.217.211.34/api/v1'}
  execFileSync(process.execPath,[vite,'build'],{cwd:old, env})
  // Rebuilding the current source produces a different real worker and marker.
  // This additional build tests consent and activation on a client with the new UI.
  const next = path.join(tmp,'next-dist')
  execFileSync(process.execPath,[vite,'build','--outDir',next],{cwd:frontend,env:{...env,GITHUB_SHA:marker(release).commit}})
  assert.notEqual(marker(next).builtAt,marker(release).builtAt)
  assert.notEqual(mainScript(release),mainScript(next))
  return {old:path.join(old,'dist'),next}
}

const mime = { '.html':'text/html', '.js':'application/javascript', '.css':'text/css',
  '.webmanifest':'application/manifest+json', '.png':'image/png', '.json':'application/json' }
function serve(state) {
  return http.createServer((req,res)=>{
    const u = new URL(req.url,'http://localhost')
    const rel = u.pathname.slice(scope.length) || 'index.html'
    if (!u.pathname.startsWith(scope) || !/^(assets\/)?[\w.-]+$/.test(rel)) {
      res.writeHead(404).end()
      return
    }
    const file = path.join(state.dir,rel)
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {res.writeHead(404).end();return}
    if (rel==='version.json') state.manifests.push({url:u.href,cache:req.headers['cache-control']})
    res.writeHead(200,{'Content-Type':mime[path.extname(rel)] || 'application/octet-stream',
      'Cache-Control':'no-cache','Content-Length':fs.statSync(file).size})
    fs.createReadStream(file).pipe(res)
  })
}

async function waitFor(test, message) {
  const end = Date.now()+25000
  while (!await test()) {
    if (Date.now()>end) throw new Error(message)
    await new Promise(resolve=>setTimeout(resolve,100))
  }
}

async function fixtureApi(context,origin) {
  let logins=0
  const records=[]
  await context.route('**/*',async route=>{
    const request=route.request(), u=new URL(request.url())
    if (!u.pathname.startsWith('/api/v1/')) {
      return u.origin===origin ? route.continue() : route.abort()
    }
    const headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Methods':'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers':'Authorization, Content-Type','Cache-Control':'no-store'}
    if (request.method()==='OPTIONS') return route.fulfill({status:204,headers})
    const key=u.pathname.slice('/api/v1/'.length)
    const data={health:{status:'ok'},'auth/login':{access_token:'synthetic-upgrade-token'},holdings:[],signals:[],
      'system/status':{version:'1.1',latest_run:null,sources:{},scheduler_enabled:false},
      settings:{high_level:70,overbought:80,interval_minutes:30,brief_time:'14:30',api_daily_limit:30,message_daily_limit:5},
      history:{transitions:[],briefings:[],deliveries:[]}}
    if (key==='auth/login') logins++
    if (!Object.hasOwn(data,key)) return route.fulfill({status:404,headers,json:{error:'unknown fixture endpoint'}})
    if (!['health','auth/login'].includes(key)) {
      assert.equal(request.headers().authorization,'Bearer synthetic-upgrade-token')
      records.push(key)
    }
    return route.fulfill({headers,json:{data:data[key]}})
  })
  return {logins:()=>logins,records}
}

async function browserTest(state,url,dirs) {
  // Block any external connection that escapes page routing, including worker-originated fetches.
  const browser=await chromium.launch({headless:true, args:['--proxy-server=http://127.0.0.1:1','--proxy-bypass-list=localhost;127.0.0.1']})
  try {
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'zh-CN'})
    const fixture=await fixtureApi(context,new URL(url).origin)
    let page=await context.newPage()
    const open=async()=>{await page.goto(url,{waitUntil:'domcontentloaded'});await page.getByLabel('管理员密码',{exact:true}).waitFor()}
    const signIn=async()=>{
      await page.getByLabel('管理员密码',{exact:true}).fill('synthetic-local-test')
      await page.getByRole('button',{name:'登录',exact:true}).click()
      await page.getByRole('link',{name:'设置',exact:true}).last().click()
      await page.getByRole('heading',{name:'设置',exact:true}).waitFor()
    }
    await open()
    await page.evaluate(()=>navigator.serviceWorker.ready)
    await page.waitForFunction(()=>!!navigator.serviceWorker.controller)
    assert.match(await page.locator('script[type=module]').first().getAttribute('src'),new RegExp(mainScript(dirs.old)))
    await signIn()
    assert.equal(fixture.logins(),1)
    // A V1 client has no version prompt. Closing and reopening it permits the
    // browser's native worker update lifecycle to install the next release.
    state.dir=release
    await page.close()
    page=await context.newPage()
    await open()
    await waitFor(()=>page.evaluate(async ([legacy,current])=>{
      const reg=await navigator.serviceWorker.getRegistration()
      if (reg?.installing || reg?.waiting || reg?.active?.state!=='activated') return false
      const pools=await Promise.all((await caches.keys()).map(async key=>(await caches.open(key)).keys()))
      const paths=pools.flat().map(r=>new URL(r.url).pathname)
      return paths.includes(current) && !paths.includes(legacy)
    },[scope+mainScript(dirs.old),scope+mainScript(release)]),'legacy worker did not activate its successor')
    await page.close() // release the last legacy client, allowing the waiting worker to activate
    page=await context.newPage()
    const beforeStart=state.manifests.length
    await open()
    assert.equal(await page.locator('script[type=module]').first().getAttribute('src'),scope+mainScript(release),'first launch after activation serves the new application')
    await waitFor(()=>state.manifests.length>beforeStart,'no online version check at startup')
    await page.waitForFunction(()=>!!navigator.serviceWorker.controller)
    await signIn()
    const check=page.getByRole('button',{name:'检查更新',exact:true})
    const update=page.getByRole('button',{name:'立即更新',exact:true})
    const heading=page.getByRole('heading',{name:'设置',exact:true})
    await check.waitFor()
    assert.ok(fixture.records.includes('holdings'))
    assert.equal(fixture.logins(),2)
    await page.evaluate(()=>{window.upgradeTestSession=crypto.randomUUID()})
    const session=await page.evaluate(()=>window.upgradeTestSession)
    state.dir=dirs.next
    const beforeForeground=state.manifests.length
    // Headless Chromium keeps tabs visible. Exercise the browser event consumed by
    // the app while the document is visible. Worker, network and UI remain real.
    await page.evaluate(()=>{
      if(document.visibilityState!=='visible') throw new Error('the test page must be visible')
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await waitFor(()=>state.manifests.length>beforeForeground,'foreground event did not check the online version')
    await update.waitFor()
    await page.getByRole('button',{name:'稍后',exact:true}).click()
    await update.waitFor({state:'hidden'})
    assert.equal(await page.evaluate(()=>window.upgradeTestSession),session)
    await heading.waitFor()
    const beforeManual=state.manifests.length
    await check.click()
    await waitFor(()=>state.manifests.length>beforeManual,'manual check did not access the online version')
    await update.waitFor()
    assert.equal(fixture.logins(),2)
    await waitFor(()=>page.evaluate(async()=>(await navigator.serviceWorker.getRegistration())?.waiting?.state==='installed'),'user-controlled update did not become ready')
    await update.click()
    await page.waitForFunction(expected=>document.querySelector('script[type=module]')?.getAttribute('src')===expected,scope+mainScript(dirs.next))
    await page.getByLabel('管理员密码',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>window.upgradeTestSession),undefined)
    assert.ok(state.manifests.every(r=>r.url.includes('?t=') && /no-cache|no-store/.test(r.cache||'')))
    const urls=await page.evaluate(async()=>{
      const pools=await Promise.all((await caches.keys()).map(async key=>(await caches.open(key)).keys()))
      return pools.flat().map(request=>new URL(request.url).pathname)
    })
    assert.ok(urls.includes(scope+mainScript(dirs.next)))
    assert.ok(!urls.includes(scope+mainScript(dirs.old)),'legacy entry is still cached')
    assert.ok(!urls.some(u=>u.startsWith('/api/v1/') || u.endsWith('/version.json')),'private or mutable data was precached')
    console.log('PASS legacy worker migration, startup/foreground/manual version checks, authenticated deferral, consented activation and offline cache cleanup')
  } finally {await browser.close()}
}

async function main() {
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'stock-radar-upgrade-'))
  let server
  try {
    if (args['published-url']) await checkPublished(args['published-url'])
    const dirs=builds(tmp), state={dir:dirs.old,manifests:[]}
    server=serve(state)
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)})
    await browserTest(state,'http://127.0.0.1:'+server.address().port+scope,dirs)
  } finally {
    if (server?.listening) {server.closeAllConnections();await new Promise(resolve=>server.close(resolve))}
    fs.rmSync(tmp,{recursive:true,force:true})
  }
}
main().catch(error=>{console.error(error);process.exitCode=1})
