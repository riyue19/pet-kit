import { ref, type Ref } from 'vue'
import { useMessage } from 'naive-ui'
import { errorText } from './store'

/**
 * A button's busy flag plus the usual feedback: `run` sets `busy` while `fn` runs, shows `done` when it
 * succeeds and the error text when it throws. Returns whether it succeeded. Call in a component's setup.
 */
export function useTask(): { busy: Ref<boolean>; run: (fn: () => Promise<unknown>, done?: string) => Promise<boolean> } {
  const message = useMessage()
  const busy = ref(false)
  async function run(fn: () => Promise<unknown>, done?: string): Promise<boolean> {
    busy.value = true
    try {
      await fn()
      if (done) message.success(done)
      return true
    } catch (e) {
      message.error(errorText(e), { duration: 6000 })
      return false
    } finally {
      busy.value = false
    }
  }
  return { busy, run }
}
