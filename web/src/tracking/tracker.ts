import type { IngestResponse, Properties, RawEvent, TrackedEvent, TrackingPlan } from "./types";
import { validateEvent } from "./validate";

export interface Transport {
  send(events: RawEvent[]): Promise<IngestResponse>;
  /** 頁面關閉時盡力送出，不等回應 */
  beacon?(events: RawEvent[]): boolean;
}

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface TrackerOptions {
  /** 共用 property platform 的值；每個平台有自己的 anonymous_id，就像不同裝置 */
  platform: string;
  plan: TrackingPlan;
  transport: Transport;
  storage: KeyValueStore;
  flushIntervalMs?: number;
  maxBatch?: number;
  now?: () => Date;
  newId?: () => string;
}

type Listener = (event: TrackedEvent) => void;

const randomId = () => crypto.randomUUID();

/**
 * 埋點 SDK。不綁定任何事件：事件名稱與 property 都由呼叫端決定，
 * 送出前和 L1 比對並記下 warning，但不會擋下事件（schema-on-read，由 L2 決定怎麼處理）。
 */
export class Tracker {
  readonly platform: string;
  readonly plan: TrackingPlan;
  private readonly transport: Transport;
  private readonly storage: KeyValueStore;
  private readonly flushIntervalMs: number;
  private readonly maxBatch: number;
  private readonly now: () => Date;
  private readonly newId: () => string;
  private queue: TrackedEvent[] = [];
  private listeners = new Set<Listener>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushing: Promise<void> | null = null;

  constructor(options: TrackerOptions) {
    this.platform = options.platform;
    this.plan = options.plan;
    this.transport = options.transport;
    this.storage = options.storage;
    this.flushIntervalMs = options.flushIntervalMs ?? 500;
    this.maxBatch = options.maxBatch ?? 20;
    this.now = options.now ?? (() => new Date());
    this.newId = options.newId ?? randomId;
    if (!this.storage.getItem(this.key("anonymous_id"))) {
      this.storage.setItem(this.key("anonymous_id"), `anon-${this.newId()}`);
    }
  }

  private key(name: string): string {
    return `grainline.${this.platform}.${name}`;
  }

  get anonymousId(): string {
    return this.storage.getItem(this.key("anonymous_id"))!;
  }

  get userId(): string | null {
    return this.storage.getItem(this.key("user_id"));
  }

  /** 登入成功後呼叫，之後的事件都帶 user_id；登入前的事件由 L2 身分合併回溯歸戶 */
  identify(userId: string): void {
    this.storage.setItem(this.key("user_id"), userId);
  }

  /** 登出：之後的事件不再帶 user_id，anonymous_id（裝置）不變 */
  logout(): void {
    this.storage.removeItem(this.key("user_id"));
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  track(eventName: string, properties: Properties = {}): TrackedEvent {
    const props: Properties = { platform: this.platform, ...properties };
    const tracked: TrackedEvent = {
      event: {
        event_id: this.newId(),
        event_name: eventName,
        timestamp: this.now().toISOString(),
        anonymous_id: this.anonymousId,
        user_id: this.userId,
        tracking_plan_version: this.plan.version,
        properties: props,
      },
      status: "queued",
      clientWarnings: validateEvent(this.plan, eventName, props),
      serverWarnings: [],
    };
    this.queue.push(tracked);
    this.emit(tracked);
    if (this.queue.length >= this.maxBatch) {
      void this.flush();
    } else {
      this.schedule();
    }
    return tracked;
  }

  private schedule(): void {
    if (this.timer === null) {
      this.timer = setTimeout(() => {
        this.timer = null;
        void this.flush();
      }, this.flushIntervalMs);
    }
  }

  private emit(tracked: TrackedEvent): void {
    for (const listener of this.listeners) listener(tracked);
  }

  /** 送出排隊中的事件。失敗的會留在佇列下次重送；伺服器依 event_id 去重，所以重送是安全的。 */
  async flush(): Promise<void> {
    if (this.flushing) await this.flushing;
    if (this.queue.length === 0) return;
    const batch = this.queue.splice(0, this.maxBatch);
    this.flushing = this.send(batch);
    try {
      await this.flushing;
    } finally {
      this.flushing = null;
    }
    if (this.queue.some((t) => t.status === "queued")) this.schedule();
  }

  private async send(batch: TrackedEvent[]): Promise<void> {
    try {
      const response = await this.transport.send(batch.map((t) => t.event));
      const warnings = new Map(response.warnings.map((w) => [w.event_id, w.messages]));
      for (const tracked of batch) {
        tracked.status = "sent";
        tracked.serverWarnings = warnings.get(tracked.event.event_id) ?? [];
        this.emit(tracked);
      }
    } catch {
      for (const tracked of batch) {
        tracked.status = "failed";
        this.emit(tracked);
      }
      this.queue.unshift(...batch);
    }
  }

  /** 頁面關閉前呼叫 */
  flushWithBeacon(): void {
    if (this.queue.length === 0 || !this.transport.beacon) return;
    if (this.transport.beacon(this.queue.map((t) => t.event))) this.queue = [];
  }
}
