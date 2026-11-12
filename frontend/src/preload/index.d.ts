import type { CodeBridge, ConsoleBridge, PetBridge } from '../shared/ipc'

declare global {
  interface Window {
    pet: PetBridge
    petConsole: ConsoleBridge
    petCode: CodeBridge
  }
}

export {}
