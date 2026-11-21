import { defineConfig } from 'vite'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import process from 'node:process'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

const base = '/stock-radar/'
const proxy = { '/api': { target: 'http://127.0.0.1:8765' } }
const project = fileURLToPath(new URL('.', import.meta.url))
const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
function buildCommit(): string {
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], { cwd: project, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return 'local'
  }
}
const release = { version, commit: buildCommit(), builtAt: new Date().toISOString() }

export default defineConfig({
  base,
  define: { __STOCK_RADAR_BUILD__: JSON.stringify(release) },
  plugins: [
    vue(),
    {
      name: 'stock-radar-version',
      generateBundle() {
        this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify(release) + '\n' })
      },
      configureServer(server) {
        server.middlewares.use(base + 'version.json', (_req, res) => {
          res.setHeader('Content-Type', 'application/json')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify(release))
        })
      },
    },
    VitePWA({
      injectRegister: false,
      registerType: 'prompt',
      includeAssets: ['icon-180.png'],
      manifest: {
        id: base,
        name: '持仓雷达',
        short_name: '持仓雷达',
        lang: 'zh-CN',
        start_url: base,
        scope: base,
        display: 'standalone',
        background_color: '#f6f8fb',
        theme_color: '#173d76',
        icons: [192, 512].map((size) => ({
          src: 'icon-' + size + '.png',
          sizes: size + 'x' + size,
          type: 'image/png',
          purpose: 'any maskable',
        })),
      },
      workbox: {
        importScripts: ['sw-upgrade.js'],
        clientsClaim: true,
        skipWaiting: false,
        cleanupOutdatedCaches: true,
        navigateFallback: 'index.html',
        navigateFallbackAllowlist: [/^\/stock-radar\/(?:index\.html)?$/],
        globPatterns: ['**/*.{js,css,html,png,woff2,ttf}'],
        runtimeCaching: [],
      },
    }),
  ],
  server: { host: '127.0.0.1', proxy },
  preview: { host: '127.0.0.1', proxy },
})
