import { createApp } from 'vue'
import App from './App.vue'
import './console.css'

const app = createApp(App)
// Uncaught component errors: print with stack so the main process records them in the frontend log.
app.config.errorHandler = (err, _instance, info) => {
  console.error(`[console] ${info}:`, err instanceof Error ? (err.stack ?? err.message) : String(err))
}
app.mount('#app')
