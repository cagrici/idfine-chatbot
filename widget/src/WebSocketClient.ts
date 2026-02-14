import type { WSIncoming, WSMessage } from "./types";

export class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private sessionId: string;
  private onMessage: (data: WSIncoming) => void;
  private onOpen: () => void;
  private onClose: () => void;
  private sourceGroupId?: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private pingInterval: ReturnType<typeof setInterval> | null = null;

  constructor(
    url: string,
    sessionId: string,
    onMessage: (data: WSIncoming) => void,
    onOpen: () => void,
    onClose: () => void,
    sourceGroupId?: string
  ) {
    this.url = url;
    this.sessionId = sessionId;
    this.onMessage = onMessage;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.sourceGroupId = sourceGroupId;
  }

  connect(): void {
    const wsUrl = this.url.replace(/^http/, "ws");
    const sgParam = this.sourceGroupId ? `?sg=${this.sourceGroupId}` : "";
    this.ws = new WebSocket(`${wsUrl}/ws/widget/${this.sessionId}${sgParam}`);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.startPing();
      this.onOpen();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WSIncoming;
        this.onMessage(data);
      } catch {
        // Ignore parse errors
      }
    };

    this.ws.onclose = () => {
      this.stopPing();
      this.onClose();
      this.tryReconnect();
    };

    this.ws.onerror = () => {
      // Error handling - onclose will be called after this
    };
  }

  send(message: WSMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  disconnect(): void {
    this.maxReconnectAttempts = 0; // Prevent reconnection
    this.stopPing();
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
    }
    this.ws?.close();
    this.ws = null;
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private tryReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;

    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
    this.reconnectAttempts++;

    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private startPing(): void {
    this.pingInterval = setInterval(() => {
      this.send({ type: "ping" });
    }, 30000);
  }

  private stopPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }
}
