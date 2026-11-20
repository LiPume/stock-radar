import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

const base = '/stock-radar/'
const proxy = { '/api': { target: 'http://127.0.0.1:8765' } }

export default defineConfig({
  base,
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
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
