import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  main: {
    // sourcemap: log lines from the main process resolve to src/main/*.ts (see logger.ts).
    build: { externalizeDeps: true, sourcemap: true }
  },
  preload: {
    build: {
      // Sandboxed preloads cannot require local chunks, so each entry must be self-contained.
      isolatedEntries: true,
      externalizeDeps: false,
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/preload/index.ts'),
          console: resolve(__dirname, 'src/preload/console.ts'),
          code: resolve(__dirname, 'src/preload/code.ts')
        }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    publicDir: resolve(__dirname, 'public'),
    plugins: [vue()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html'),
          console: resolve(__dirname, 'src/renderer/console.html'),
          code: resolve(__dirname, 'src/renderer/code.html')
        }
      }
    }
  }
})
