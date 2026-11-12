/** Mirrors backend/api/llm_routes.py (snake_case on purpose: passed through unchanged). */

export interface LlmProfile {
  name: string
  base_url: string
  model: string
  max_tokens: number | null
  temperature: number | null
  top_p: number | null
  presence_penalty: number | null
  frequency_penalty: number | null
  timeout: number
  /** True: request with stream=true and join the chunks (some relays only answer, or only stay within their timeout, when streaming). The reply is still handled whole. */
  stream: boolean
  extra: Record<string, unknown>
  /** How this endpoint enables the model's own web search: 'off' | 'google_function' (new-api relays → Gemini Google Search) | 'openai_options' (web_search_options). */
  web_search: 'off' | 'google_function' | 'openai_options'
}

export interface KeyStatus {
  set: boolean
  hint: string | null
}

export interface LlmRoles {
  chat: string | null
  vision: string | null
  /** Memory summarization; null = use the chat profile. */
  memory: string | null
}

/** Backup profiles per role, best first (at most 2: 2nd and 3rd place). */
export interface LlmFallbacks {
  chat: string[]
  vision: string[]
  /** Only used when roles.memory is set; otherwise memory follows the chat chain. */
  memory: string[]
}

export interface LlmState {
  profiles: (LlmProfile & { key: KeyStatus })[]
  roles: LlmRoles
  fallbacks: LlmFallbacks
  /** Global switch for the chat model's web search (per-profile method above). */
  web_search: boolean
}

export interface LlmSave {
  profiles: (LlmProfile & { renamed_from?: string | null })[]
  roles: LlmRoles
  fallbacks?: LlmFallbacks
  web_search?: boolean
}

export interface LlmTestRequest {
  kind: 'chat' | 'vision'
  profile: LlmProfile
  key_profile?: string | null
}

export interface LlmTestResult {
  ok: boolean
  latency_ms: number | null
  model: string | null
  reply: string | null
  error: string | null
}

/** Mirrors backend/core/voice_config.py and backend/api/voice_routes.py. */
export interface AsrConfig {
  /** Microphone always listening when true; when false the pet never opens the microphone. */
  listen: boolean
  /** Recognition language; 'auto' may turn breaths/noise into Japanese/Korean syllables. */
  language: 'zh' | 'auto' | 'en' | 'ja' | 'ko' | 'yue'
  /** Shorter recognized results (punctuation removed) and pure fillers (嗯/啊) are dropped. 1..10 */
  min_chars: number
  /** Results of 5 characters or fewer from audio shorter than this are dropped. 0..2; 0 turns it off. */
  short_min_seconds: number
  model_dir: string
  threads: number
  vad_threshold: number
  /** Silence that ends an utterance, 0.2..3 */
  min_silence_seconds: number
  /** Sounds shorter than this are not speech, 0.1..1 */
  min_speech_seconds: number
  /** Longest single utterance, 5..60 */
  max_speech_seconds: number
  /** What counts as a short result for short_min_seconds, 1..20 */
  short_chars: number
  /** Drop results made only of filler sounds. */
  drop_fillers: boolean
  /** Results exactly equal to one of these are dropped (max 50, 30 chars each). */
  ignore_phrases: string[]
  /** Talking over the pet cuts it off; when false it finishes and later replies queue up. */
  barge_in: boolean
  barge_in_seconds: number
}

export interface MiniMaxConfig {
  base_url: string
  model: string
  voice_id: string
  speed: number
  vol: number
  pitch: number
  timeout: number
}

export interface CosyVoiceConfig {
  url: string
  model: string
  voice: string
  volume: number
  rate: number
  pitch: number
  /** Send the expression's emotion as an instruction (only a few voices support it). */
  emotion_instruct: boolean
  instruction: string
  timeout: number
}

export interface OpenAITtsConfig {
  base_url: string
  model: string
  voice: string
  speed: number
  response_format: 'pcm' | 'wav'
  sample_rate: number
  extra: Record<string, unknown>
  timeout: number
}

export type TtsProviderName = 'minimax' | 'cosyvoice' | 'openai'

export interface TtsConfig {
  enabled: boolean
  provider: TtsProviderName
  /** 'same' reads the bubble text; 'ja'/'en': the LLM adds a spoken line in that language, bubble stays Chinese. */
  speech_language: 'same' | 'ja' | 'en'
  minimax: MiniMaxConfig
  cosyvoice: CosyVoiceConfig
  openai: OpenAITtsConfig
  /** Expression label -> TTS emotion; labels named like an emotion map to it implicitly. */
  emotions: Record<string, string>
}

export interface VoiceState {
  asr: AsrConfig
  tts: TtsConfig
  /** Local ASR model files present; listening stays off until they are. */
  models_ready: boolean
  /** Command (run in backend/) that downloads the ASR models. */
  fetch_command: string
  /** Key of the saved provider. */
  key: KeyStatus
  /** Key of every provider (each has its own). */
  keys: Record<TtsProviderName, KeyStatus>
  /** Allowed values for TtsConfig.emotions. */
  emotions: string[]
  /** Allowed values for MiniMaxConfig.model. */
  models: string[]
  /** Suggested values for CosyVoiceConfig.model (others may be typed). */
  cosyvoice_models: string[]
}

/** One entry of GET /api/voice/heard (newest first). */
export interface HeardEntry {
  ts: number
  text: string
  seconds: number
  /** Recorded with push-to-talk. */
  manual: boolean
  sent: boolean
  /** Why it was dropped; null when sent. */
  reason: string | null
}

/** Mirrors backend/core/prompt_config.py (config/prompt.yaml). */
export interface KnowledgeEntry {
  title: string
  content: string
  enabled: boolean
  mode: 'always' | 'keyword'
  keywords: string[]
}

export interface PromptConfig {
  /** Order of the blocks of the system prompt (PromptSectionInfo.key); empty = built-in order. */
  order: string[]
  format_rules: string
  knowledge: KnowledgeEntry[]
  expression_notes: Record<string, string>
  motion_notes: Record<string, string>
  search_keywords: string[]
  /** Template key -> replacement for that whole block of the prompt; missing = built-in text. */
  overrides: Record<string, string>
  /** keyword: a command is described only when the user's words contain its name; always: every turn. */
  skills_mode: 'keyword' | 'always'
  /** Trigger words for actions that have no name on the 指令 page. */
  tool_keywords: Record<string, string[]>
  search_use_fallback: boolean
  speech_repair: boolean
}

export interface PromptSection {
  key: string
  title: string
  text: string
  /** Console page where it is edited; null = built in. */
  source: 'persona' | 'prompt' | 'memory' | 'labels' | 'stickers' | 'skills' | 'llm' | 'screen' | 'status' | null
  chars: number
  /** Template key this block is rendered from (editable via PromptConfig.overrides); null = not a template. */
  template: string | null
}

export interface PromptTemplate {
  key: string
  title: string
  /** Built-in text of the whole block; {name} marks generated content. */
  default: string
  placeholders: { name: string; desc: string }[]
  /** Placeholders the block does not work without. */
  required: string[]
}

/** One block the system prompt can contain (always sent, or on a fixed condition). */
export interface PromptSectionInfo {
  key: string
  title: string
  /** When the block is sent; decided by the program, shown only. */
  condition: string
  always: boolean
  /** Template keys edited under this block. */
  templates: string[]
  source: string | null
}

export interface PromptTool {
  name: string
  title: string
  defaults: string[]
}

/** POST /api/prompt/preview, and (with the optional fields) PromptState.last. */
export interface PromptView {
  sections: PromptSection[]
  history: { role: string; content: string }[]
  user: string
  notes: string[]
  search: boolean
  speech_language: string | null
  model: string | null
  total_chars: number
  ts?: number
  event_id?: string
  event_type?: string
  /** Raw model reply (truncated). */
  raw?: string | null
  error?: string | null
  speech_repaired?: boolean
}

/** GET/PUT /api/prompt */
export interface PromptState {
  config: PromptConfig
  expressions: string[]
  motions: string[]
  pet_connected: boolean
  max_knowledge_chars: number
  templates: PromptTemplate[]
  /** Every block, in the saved order. */
  sections: PromptSectionInfo[]
  default_order: string[]
  /** Names of the usable commands on the 指令 page. */
  skill_labels: string[]
  tools: PromptTool[]
  last: PromptView | null
}

/** Mirrors backend/core/status_config.py (config/status.yaml). */
export interface StatusTier {
  /** The tier covers values from here (inclusive) up to the next tier's min. */
  min: number
  name: string
  /** Extra prompt text while the value is in this tier. */
  prompt: string
}

export interface StatusStat {
  id: string
  name: string
  meaning: string
  rule: string
  min: number
  max: number
  initial: number
  /** Largest change one reply can make. */
  max_up: number
  max_down: number
  /** Total rise allowed per day; 0 = no limit. */
  daily_up: number
  color: string
  tiers: StatusTier[]
}

export interface StatusConfig {
  enabled: boolean
  stats: StatusStat[]
}

export interface StatusLive {
  id: string
  value: number
  tier: string | null
  gained_today: number
}

export interface StatusRecent {
  ts: number
  id: string
  name: string
  delta: number
  value: number
  reason: string
  tier: string | null
}

/** GET/PUT /api/status, POST /api/status/value */
export interface StatusState {
  config: StatusConfig
  stats: StatusLive[]
  /** Newest first. */
  recent: StatusRecent[]
  /** Limits of the form. Missing from a backend older than these fields. */
  max_stats?: number
  max_tiers?: number
}

export interface VoiceSave {
  asr: AsrConfig
  tts: TtsConfig
}

export interface VoiceTestRequest {
  text?: string | null
  /** Unsaved form values; the saved config is used when omitted. */
  tts?: TtsConfig | null
  expression?: string | null
}

export interface VoiceTestResult {
  ok: boolean
  first_audio_ms: number | null
  error: string | null
}

export interface VoiceInfo {
  voice_id: string
  name: string
  kind: 'system_voice' | 'voice_cloning' | 'voice_generation'
}

/** One log line (backend: backend/core/logs.py; frontend: src/main/logger.ts uses the same shape). */
export interface LogEntry {
  seq: number
  /** Unix seconds. */
  ts: number
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  /** Python logger name, or for the frontend: 'main' | 'pet' | 'console' | 'code'. */
  logger: string
  /** Source file (relative to backend/ or frontend/ when possible) and line. */
  file: string
  line: number
  func: string
  msg: string
  /** Event being handled when the line was written; links a log line to its conversation. */
  event_id: string | null
  /** Stack trace, if any. */
  exc: string | null
}

export interface LogQuery {
  /** Only entries with seq > after (incremental polling). */
  after?: number
  /** Minimum level. */
  level?: LogEntry['level']
  /** Logger name prefix. */
  logger?: string
  event_id?: string
  /** Case-insensitive substring of msg or stack. */
  q?: string
  /** 1..1000, default 200 (newest matching entries). */
  limit?: number
}

export interface LogsResponse {
  entries: LogEntry[]
  last_seq: number
  /** Folder holding the full JSON-lines log files. */
  log_dir: string | null
}

export interface EventResult {
  output?: { text: string; expression: string | null; motion: string | null }
  error?: string
}

export interface EventEntry {
  seq: number
  id: string
  type: string
  ts: number
  payload: Record<string, unknown>
  /** null while the event is pending or produced no output. */
  result: EventResult | null
}

export interface EventsResponse {
  events: EventEntry[]
  last_seq: number
  /** Event types POST /api/events accepts. */
  injectable: string[]
  /** Every event type there is. Missing from a backend older than this field. */
  types?: string[]
}

/** POST /api/events. Payloads: user.text {text}; voice.utterance {text, duration?}; pet.interaction {kind:'click', area:'head'|'body'}. */
export interface InjectRequest {
  type: string
  payload: Record<string, unknown>
}

/** Mirrors backend/core/persona.py Persona (config/persona.yaml). */
export interface Persona {
  name: string
  system_prompt: string
  history_turns: number
  max_reply_chars: number
}

/** Mirrors backend/core/memory_config.py (config/backend.yaml `memory`). */
export interface MemoryConfig {
  enabled: boolean
  /** Summarize after this many new exchanges, 5..200. */
  summarize_every_turns: number
  /** Memories added to each prompt, 0..20 (pinned ones always, on top). */
  recall_memories: number
  /** Older conversation lines added to each prompt, 0..10. */
  recall_messages: number
  /** 0.1..0.95 */
  min_similarity: number
  model_dir: string
  /** Absolute folder or .db file; '' = default (%LOCALAPPDATA%/pet-kit/memory.db). Changing it moves the database. */
  db_path: string
}

export interface SummaryResult {
  ok: boolean
  added: number
  updated: number
  deleted: number
  /** Conversation lines that were summarized. */
  lines: number
  error: string | null
  /** Unix seconds of the run. */
  at: number | null
}

/** GET/PUT /api/memory */
export interface MemoryState {
  config: MemoryConfig
  stats: {
    messages: number
    memories: number
    unsummarized_turns: number
    /** Local embedding model loaded; false = recall/semantic search unavailable (history still saved). */
    embed_ready: boolean
    db_path: string
    last_summary: SummaryResult | null
  }
  /** Command (run in backend/) that downloads the embedding model. */
  fetch_command: string
  embed_model_dir: string
  /** Set after a save that moved or switched the database (show it to the user). */
  notice: string | null
}

/** GET/POST/PUT /api/memories */
export interface MemoryItem {
  id: number
  text: string
  pinned: boolean
  source: 'summary' | 'manual'
  created: number
  updated: number
  /** Similarity to the query; semantic search only. */
  score: number | null
}

export interface ChatMessage {
  id: number
  ts: number
  role: 'user' | 'assistant'
  text: string
  /** Event type that started the exchange: user.text | voice.utterance | pet.interaction */
  source: string
  event_id: string | null
  expression: string | null
  motion: string | null
  summarized: boolean
}

/** GET /api/history: newest first; pass the smallest id as before_id for the next page. */
export interface HistoryPage {
  messages: ChatMessage[]
  has_more: boolean
}

export interface HistoryDay {
  /** Local date YYYY-MM-DD */
  date: string
  count: number
}

/** POST /api/history/delete: exactly one of ids / date / all / summarized. */
export interface HistoryDeleteRequest {
  ids?: number[]
  date?: string
  all?: boolean
  /** Delete messages already summarized into memories, except the newest `keep_recent`. */
  summarized?: boolean
  keep_recent?: number
}

/** Mirrors backend/core/screen_config.py (config/backend.yaml `screen`). */
export interface ScreenConfig {
  enabled: boolean
  mode: 'quiet' | 'normal' | 'chatty'
  /** Comment on fullscreen games/videos (meetings, recording, streaming stay silent regardless). */
  allow_fullscreen: boolean
  /** 0..600 */
  quiet_after_user_seconds: number
  /** 0.5..30 */
  poll_seconds: number
}

/** Mirrors backend/core/screen_config.py (config/backend.yaml `proactive`). */
export interface ProactiveConfig {
  enabled: boolean
  mode: 'quiet' | 'normal' | 'chatty'
  /** Chat when the user is at the computer but hasn't talked to the pet for this long. */
  idle_chat: boolean
  /** 3..240 */
  idle_after_minutes: number
  /** Greet the user when they return after being away from the keyboard. */
  welcome_back: boolean
  /** 3..240, minutes away before a return counts. */
  away_minutes: number
  /** Morning / lunch / dinner / late-night greetings, each at most once a day. */
  greetings: boolean
  /** Play a random motion/expression after the user hasn't interacted with the pet for a while (no LLM). */
  idle_action: boolean
  /** 1..240, minutes without interaction before (and between) idle actions. */
  idle_action_minutes: number
  /** Motion labels to pick from, max 16. */
  idle_action_motions: string[]
  /** Expression labels to pick from, max 16. */
  idle_action_expressions: string[]
}

/** What each proactive mode means (keys: quiet / normal / chatty). */
export interface ChatCadence {
  min_gap_seconds: number
  max_per_hour: number
  idle_repeat_seconds: number
}

/** config/backend.yaml `environment`. */
export interface EnvironmentConfig {
  /** e.g. 杭州 / 北京 朝阳; '' = no weather. Max 40 chars. */
  city: string
  weather: boolean
}

/** config/privacy.yaml. Processes: exact exe name, case-insensitive. Titles: case-insensitive substring. */
export interface PrivacyConfig {
  /** Never captured or commented on. */
  block_processes: string[]
  block_titles: string[]
  /** Pet stays silent while these are in front (meetings, recording, streaming). */
  quiet_processes: string[]
  quiet_titles: string[]
}

export interface ScreenCadence {
  dwell_seconds: number
  per_content_seconds: number
  min_gap_seconds: number
  max_per_hour: number
}

/** GET/PUT /api/screen */
export interface ScreenState {
  screen: ScreenConfig
  proactive: ProactiveConfig
  /** Labels of the current model; both empty when the pet is not connected. */
  labels: { expressions: string[]; motions: string[] }
  chat_cadences: Record<string, ChatCadence>
  environment: EnvironmentConfig
  privacy: PrivacyConfig
  /** What each mode means (keys: quiet / normal / chatty). */
  cadences: Record<string, ScreenCadence>
  status: {
    /** Last foreground window that wasn't the pet/console; blocked windows show title （隐私窗口）. */
    foreground: { app: string; title: string; fullscreen: boolean; blocked: boolean; quiet: boolean } | null
    spoken_last_hour: number
    /** Vision calls this hour (each look costs, whether or not the pet then speaks). */
    looks_last_hour: number
    last_comment_at: number | null
    last_comment_app: string | null
    last_comment_text: string | null
    /** llm.roles.vision; null = screen comments can't work. */
    vision_profile: string | null
    pet_connected: boolean
    /** Seconds since the last keyboard/mouse input; null if it can't be read. */
    system_idle_seconds: number | null
    last_proactive_at: number | null
    last_proactive_kind: string | null
    last_proactive_text: string | null
  }
}

export interface ScreenSave {
  screen: ScreenConfig
  proactive: ProactiveConfig
  environment: EnvironmentConfig
  privacy: PrivacyConfig
}

/** POST /api/screen/look {delay_seconds 0..30} */
export interface ScreenLookResult {
  scheduled: boolean
  error: string | null
}

/** POST /api/screen/weather (fetches now) */
export interface WeatherResult {
  text: string
  error: string | null
}

/** Mini games (backend/core/games, /api/games). */
export type GameSide = 'player' | 'pet'
export type GameKey = 'roulette' | 'rps' | 'dice' | 'auction' | 'taboo'
export type RouletteItem = 'juice' | 'dice' | 'flash' | 'lens' | 'driver'
export type Gesture = 'rock' | 'scissors' | 'paper'

/** Set once a match is over: the pet's closing line and the affection reward (null = none). */
export interface GameEnd {
  say: string
  reward: { name: string; delta: number; value: number; reason: string } | null
}

interface GameCommon {
  end: GameEnd | null
  /** Started on the pet's own invitation. */
  invited: boolean
  use_llm: boolean
}

/** One public event of a roulette game; `seq` only grows. */
export interface RouletteLog {
  seq: number
  kind: 'load' | 'shoot' | 'item' | 'win'
  /** Who acted (load: who goes first this round; win: the winner). */
  side: GameSide
  /** shoot */
  target?: 'self' | 'opponent'
  /** shoot: the round was live; item driver: the removed round was live; load: number of live rounds. */
  live?: boolean | number
  damage?: number
  /** item */
  item?: RouletteItem
  /** item dice */
  guess?: 'big' | 'small'
  roll?: number
  ok?: boolean
  /** load */
  blank?: number
  round?: number
  /** What the pet said while doing this. */
  say?: string
}

/** What the player may see of a roulette game (never the bullet order or the pet's items). */
export interface RouletteView extends GameCommon {
  mode: 'classic' | 'custom'
  bullets: number
  round: number
  /** Who goes first in the current round (decided again every round). */
  first: GameSide
  turn: GameSide
  winner: GameSide | null
  hp: Record<GameSide, number>
  max_hp: Record<GameSide, number>
  left: number
  live: number
  blank: number
  /** The player's own items. */
  items: RouletteItem[]
  /** How many items the pet holds. */
  pet_items: number
  /** What the player saw through the magnifier of the current round: 1 live, 0 blank, null not looked. */
  peek: 0 | 1 | null
  /** The side to move armed a flash grenade. */
  boost: boolean
  seq: number
  log: RouletteLog[]
}

export interface RpsView extends GameCommon {
  /** 1, 2: showing a gesture; 3: talk, then the final throw; 4: finished. */
  step: 1 | 2 | 3 | 4
  /** Gestures shown in the finished steps. */
  picks: Record<GameSide, Gesture[]>
  /** What the player may choose now. */
  options: Gesture[]
  final: Record<GameSide, Gesture | null>
  result: GameSide | 'draw' | null
  chat: { side: GameSide; text: string }[]
  /** Exchanges of talk left before the final throw (only in step 3). */
  talk_left: number
  talk_rounds: number
}

export interface DiceBid {
  side: GameSide
  count: number
  face: number
  say?: string
}

export interface DiceReveal {
  hands: Record<GameSide, number[]>
  bid: DiceBid
  caller: GameSide
  /** How many dice on the table showed bid.face. */
  total: number
  /** Who lost a die. */
  loser: GameSide
  say?: string
}

export interface DiceView extends GameCommon {
  round: number
  /** bid: bidding; reveal: the cups are up, waiting for "next"; over: the match ended (reveal still set). */
  phase: 'bid' | 'reveal' | 'over'
  /** Who bid first in round 1 (the wheel). */
  first: GameSide
  turn: GameSide
  /** Dice each side has left. */
  dice: Record<GameSide, number>
  start_dice: number
  /** The player's own dice this round. */
  hand: number[]
  bids: DiceBid[]
  can_call: boolean
  can_raise: boolean
  /** Dice on the table this round. */
  total: number
  reveal: DiceReveal | null
  winner: GameSide | null
  history: { round: number; bid: DiceBid; caller: GameSide; total: number; loser: GameSide }[]
}

export interface AuctionRound {
  round: number
  prizes: number[]
  player: number
  pet: number
  /** null = tie: the prizes stayed on the table. */
  winner: GameSide | null
  say?: string
}

export interface AuctionView extends GameCommon {
  round: number
  rounds: number
  win_score: number
  /** Prize cards on the table now (several after a tie). */
  pot: number[]
  hand: number[]
  pet_hand: number[]
  scores: Record<GameSide, number>
  /** Prize cards not turned up yet. */
  left: number
  history: AuctionRound[]
  result: GameSide | 'draw' | null
}

export interface TabooTurn {
  side: GameSide
  kind: 'clue' | 'guess'
  text: string
  ok?: boolean
}

export interface TabooView extends GameCommon {
  describer: GameSide
  round: number
  rounds: number
  turns: TabooTurn[]
  /** Only when the player describes, or once it is over. */
  word: string | null
  taboo: string[] | null
  word_chars: number
  /** player = guessed in time; draw = not guessed. */
  result: 'player' | 'draw' | null
}

/** Every /api/games response. */
export interface GamesState {
  /** A chat LLM is configured (otherwise the pet plays the built-in strategy silently). */
  llm_available: boolean
  /** persona.yaml name. */
  pet_name: string
  /** The status bar (好感度) is on: results can change affection. */
  status_on: boolean
  settings: { voice: boolean; invite: boolean }
  /** Results per game from the player's side. */
  records: Record<GameKey, { player: number; pet: number; draw: number }>
  /** The game the pet invited the player to, if an invitation is pending. */
  invited: GameKey | null
  roulette: RouletteView | null
  rps: RpsView | null
  dice: DiceView | null
  auction: AuctionView | null
  taboo: TabooView | null
}

/** Idle fishing (backend/core/games/fishing, /api/fishing). Every fact comes from the backend. */
export type FishRarity = 'common' | 'uncommon' | 'rare' | 'legendary'

export interface FishingSettings {
  /** A cast waits a random real time between these two (seconds) before anything bites; 180..21600 / 180..43200. */
  wait_min_sec: number
  wait_max_sec: number
  /** LLM calls per day for fishing lines / notes / letters, 0..500. */
  llm_budget_per_day: number
  /** Play the whole animation for an empty cast (off = a small ripple only). */
  animate_empty: boolean
  /** Bite window for rare fish, 1000..10000 ms. */
  bite_window_ms: number
  /** Off = light tier: the HUD next to the pet shows still pictures only. */
  hud_animation: boolean
  /** The small window is shown next to the pet (false = put away). */
  hud_placed: boolean
  /** Size of the small window, 0.6..2.5. */
  hud_scale: number
}

export interface FishingBagItem {
  id: number
  species_id: string
  name: string
  rarity: FishRarity
  rarity_label: string
  template: string
  patterns: string[]
  size_cm: number
  trophy: boolean
  /** Unix seconds. */
  caught_ts: number
  location: string
  /** Coins it sells for. */
  price: number
}

/** A codex entry. Until `found`, only the shape and where to look are given. */
export interface FishingSpecies {
  id: string
  found: boolean
  template: string
  /** Place names. */
  locations: string[]
  location_ids: string[]
  name?: string
  rarity?: FishRarity
  rarity_label?: string
  patterns?: string[]
  /** Display names, e.g. ["清晨", "黄昏"] or ["全天"]. */
  time?: string[]
  weather?: string[]
  /** Baits it likes (display names). */
  baits?: string[]
  count?: number
  max_size?: number
  min_size?: number
  avg_size?: number
  first_ts?: number
  released?: number
  /** The pet's note about it; null until written. */
  story?: string | null
  /** The biggest one caught was a trophy. */
  trophy?: boolean
}

export interface FishingLogEntry {
  seq: number
  ts: number
  outcome: 'empty' | 'fish' | 'junk' | 'bottle' | 'escape'
  name: string | null
  size_cm: number | null
  location: string
  weather: string
  time_slot: string
}

/** GET /api/fishing (also `state` of a command response, and the response of settings / cast). */
export interface FishingSnapshot {
  enabled: boolean
  /** fish.yaml is broken (the last good version is in use); null = fine. */
  error: string | null
  llm_available: boolean
  coins: number
  /** Id of the current place (see `locations`). */
  location: string
  /** Id of the bait in use (see `baits`); "none" = no bait. */
  bait: string
  rod_level: number
  rod_name: string
  next_rod: { name: string; cost: number } | null
  weather: string
  time_slot: string
  /** When the line in the water was cast (Unix seconds); null = no line out, or a bite is being settled. */
  cast_ts: number | null
  /** Backend clock (Unix seconds), to show the time waited without trusting the local clock. */
  now: number
  empty_streak: number
  /** Chance (0..1) that the pet lands a rare bite on its own. */
  hook_chance: number
  settings: FishingSettings
  llm_used_today: number
  locations: { id: string; name: string; unlock_cost: number; requires_rod: string; requires_rod_level: number; unlocked: boolean }[]
  /** qty is null for "none". */
  baits: { id: string; name: string; cost: number; qty: number | null }[]
  inventory: FishingBagItem[]
  species: FishingSpecies[]
  found: number
  junk: { id: string; name: string; count: number }[]
  /** Letters read so far, newest first. */
  bottles: { sender: string; body: string; read_ts: number }[]
  /** Newest first. */
  log: FishingLogEntry[]
  totals: { casts: number; fish: number; escaped: number }
}

export type FishingCommand = 'start' | 'stop' | 'place' | 'unlock' | 'bait' | 'buy' | 'rod' | 'sell' | 'release' | 'interval'

/** POST /api/fishing/command {cmd, args, confirmed}. */
export interface FishingCommandResult {
  ok: boolean
  /** What to show the user. */
  text: string
  /** Not done: ask the user, then send again with confirmed: true. */
  need_confirm: boolean
  state: FishingSnapshot
}

/** POST /api/fishing/simulate: 10 000 simulated casts with the current place, bait and rod. */
export interface FishingSimulation {
  casts: number
  days: number
  outcomes: Record<'empty' | 'fish' | 'junk' | 'bottle', number>
  rarities: Record<FishRarity, number>
  /** Species name -> count, most caught first. */
  species: Record<string, number>
  escaped: number
  coins_per_day: number
  days_per_legendary: number | null
}

/** Builds a path with a query string accepted by BACKEND_PATH_RE. */
export function withQuery(path: '/api/logs' | '/api/events' | '/api/history' | '/api/memories' | '/api/history/summarized-count', params: Record<string, string | number | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return parts.length ? `${path}?${parts.join('&')}` : path
}

export type BackendMethod = 'GET' | 'PUT' | 'POST' | 'DELETE'

export type BackendResult<T = unknown> =
  | { ok: true; data: T }
  | { ok: false; offline: boolean; status: number | null; error: string }

/** Only these API paths may be reached through the console proxy; only logs/events/history/memories take a query string. */
export const BACKEND_PATH_RE =
  /^\/api\/(?:health|llm|llm\/key|llm\/test|voice|voice\/key(?:\?provider=(?:minimax|cosyvoice|openai))?|voice\/test|voice\/voices|voice\/heard|prompt|prompt\/preview|persona|memory|memory\/summarize|screen|screen\/look|screen\/weather|camera|camera\/look|skills|skills\/installed|privacy\/off|status|status\/value|fishing|fishing\/(?:command|settings|hook|simulate)|fishing\/hud(?:\?after=\d{1,9})?|games|games\/settings|games\/(?:roulette|rps|dice|auction|taboo)\/(?:start|quit)|games\/roulette\/(?:move|pet)|games\/rps\/(?:pick|chat)|games\/dice\/(?:move|pet|next)|games\/auction\/play|games\/taboo\/say|history\/days|history\/delete|history\/summarized-count(?:\?keep_recent=\d{1,6})?|(?:logs|events|history|memories)(?:\?[A-Za-z0-9_=&%.~*'()!-]*)?)$/
