/** Agent output (CLAUDE.md §2): expression/motion are semantic labels resolved via the model's ActionMap. */
export interface AgentOutput {
  text: string
  expression: string | null
  motion: string | null
  /** Note name of the sticker the model wants to send (config/stickers.yaml). */
  sticker?: string | null
  /** Filled in by the main process: the sticker picture as a data URL. */
  stickerUrl?: string
  tool_calls: { name: string; args: Record<string, unknown> }[]
  /** Status values that changed with this reply (config/status.yaml); shown as a pop next to the head. */
  status?: StatusChange[]
}

/** One applied change of a status value (backend core/status_config.py StatusChange). */
export interface StatusChange {
  id: string
  name: string
  /** Signed change that actually happened. */
  delta: number
  value: number
  /** Position of the new value in the stat's range, 0..1. */
  ratio: number
  reason: string
  /** #rrggbb */
  color: string
  /** Name of the tier the value moved into; null when it stayed in the same one. */
  tier: string | null
}

export type InteractionArea = 'head' | 'body'

export interface PetInteraction {
  kind: 'click'
  area: InteractionArea
}

export type LinkState = 'offline' | 'online'

export interface LinkStatus {
  state: LinkState
  thinking: boolean
}

export interface MotionRef {
  group: string
  index: number
  /** Model expression shown while this motion plays (e.g. props the motion animates but that are hidden by default). */
  expression?: string
  /** Restart when it ends, until the user engages the pet or another motion plays. */
  loop?: boolean
}

/** Per-model mapping, stored in config/models/<model dir>.yaml. */
export interface ActionMap {
  /** label -> expression Name from model3.json */
  expressions: Record<string, string>
  /** label -> motion group + index from model3.json */
  motions: Record<string, MotionRef>
  /** How long an expression is held before returning to the default face. */
  holdSeconds: number
}

export interface ModelInventory {
  expressions: string[]
  motions: { group: string; count: number }[]
}

export interface ModelActions {
  /** Model directory name, e.g. "10991"; null when no model is configured. */
  model: string | null
  available: ModelInventory
  map: ActionMap
}

/** Raw model references (not labels); used by the console's test-play buttons. */
export interface PlayCommand {
  expression?: string | null
  motion?: MotionRef | null
  /** Stop the running (possibly looping) motion. */
  stop?: boolean
}

export const LABEL_RE = /^[a-z][a-z0-9_]{0,31}$/
export const HOLD_SECONDS_RANGE = { min: 1, max: 60 }
export const DEFAULT_ACTION_MAP: ActionMap = { expressions: {}, motions: {}, holdSeconds: 6 }
export const MAX_CHAT_CHARS = 500
/** Largest picture the pet window may send (base64 chars; the backend allows 5 MB of image bytes). */
export const MAX_IMAGE_DATA_URL_CHARS = 7_000_000

/** Voice control messages from the backend (see backend/api/pet_ws.py), relayed main -> pet window. */
export type VoiceMessage =
  /** Whether the pet window should keep the microphone open. Forced false while the backend is offline. */
  | { type: 'mic'; listen: boolean }
  /** The backend holds the webcam open (shown as a red dot; always false while the backend is offline). */
  | { type: 'camera'; on: boolean; present: boolean | null; enabled: boolean }
  /** VAD: the user started / stopped talking. */
  | { type: 'hearing'; active: boolean }
  /** Recognized utterance (already sent to the agent by the backend). */
  | { type: 'heard'; text: string }
  /** The user talked over the pet: stop playback now. */
  | { type: 'interrupt' }
  /** TTS stream begins; following binary frames are 16-bit LE mono PCM at sample_rate. */
  | { type: 'audio_start'; id: number; sample_rate: number }
  | { type: 'audio_end'; id: number; error?: string }

export const MIC_SAMPLE_RATE = 16000
/** Upper bound for one microphone frame sent over IPC (bytes); the worklet sends 100 ms = 3200 bytes. */
export const MAX_MIC_FRAME_BYTES = 16000
