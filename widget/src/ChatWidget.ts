import { LOGO_DARK_DATA_URI, LOGO_LIGHT_DATA_URI } from "./assets";
import { renderMarkdown } from "./markdown";
import { getStyles } from "./styles";
import { WebSocketClient } from "./WebSocketClient";
import type { ChatMessage, Source, WidgetConfig, WSIncoming } from "./types";

export class ChatWidget {
  private shadow: ShadowRoot;
  private config: WidgetConfig;
  private messages: ChatMessage[] = [];
  private ws: WebSocketClient | null = null;
  private visitorId = "";
  private conversationId = "";
  private isOpen = false;
  private isFullscreen = false;
  private isStreaming = false;
  private currentStreamId = "";
  private currentStreamContent = "";
  private logoUrl: string;

  // DOM references
  private container!: HTMLDivElement;
  private messagesEl!: HTMLDivElement;
  private textarea!: HTMLTextAreaElement;
  private sendBtn!: HTMLButtonElement;
  private triggerBtn!: HTMLButtonElement;

  constructor(hostElement: HTMLElement, config: WidgetConfig) {
    this.config = {
      position: "bottom-right",
      brandColor: "#231f20",
      brandName: "ID Fine",
      welcomeMessage: "Merhaba! Size nasıl yardımcı olabilirim?",
      placeholder: "Mesajınızı yazın...",
      width: 380,
      height: 560,
      triggerSize: 60,
      logoVariant: "dark",
      ...config,
    };

    // Resolve logo: explicit URL > variant selection > default dark
    if (this.config.logoUrl) {
      this.logoUrl = this.config.logoUrl;
    } else {
      this.logoUrl =
        this.config.logoVariant === "light"
          ? LOGO_LIGHT_DATA_URI
          : LOGO_DARK_DATA_URI;
    }

    this.shadow = hostElement.attachShadow({ mode: "closed" });
    this.init();
  }

  private async init() {
    this.render();
    this.bindEvents();
    await this.initSession();
  }

  private render() {
    const style = document.createElement("style");
    style.textContent = getStyles(this.config);
    this.shadow.appendChild(style);

    // Trigger button - floating logo
    this.triggerBtn = document.createElement("button");
    this.triggerBtn.className = "idf-widget-trigger";
    this.triggerBtn.setAttribute("aria-label", "Sohbet aç/kapat");

    const triggerImg = document.createElement("img");
    triggerImg.src = this.logoUrl;
    triggerImg.alt = this.config.brandName || "ID Fine";
    triggerImg.draggable = false;
    this.triggerBtn.appendChild(triggerImg);

    // Close icon inside trigger (shown when open)
    const closeDiv = document.createElement("div");
    closeDiv.className = "idf-trigger-close";
    closeDiv.innerHTML = `<svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>`;
    this.triggerBtn.appendChild(closeDiv);

    this.shadow.appendChild(this.triggerBtn);

    // Chat container
    this.container = document.createElement("div");
    this.container.className = "idf-widget-container";
    this.container.innerHTML = `
      <div class="idf-widget-header">
        <div class="idf-header-info">
          <img class="idf-header-logo" src="${this.logoUrl}" alt="${this.config.brandName}">
          <div class="idf-header-text">
            <h3>${this.config.brandName} Asistan</h3>
            <div class="idf-status">Çevrimiçi</div>
          </div>
        </div>
        <div class="idf-header-actions">
          <button class="idf-widget-fullscreen" aria-label="Tam ekran">
            <svg class="idf-fs-expand" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>
            <svg class="idf-fs-shrink" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:none"><path d="M4 14h6v6m10-10h-6V4m0 6l7-7M3 21l7-7"/></svg>
          </button>
          <button class="idf-widget-close" aria-label="Kapat">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
      </div>
      <div class="idf-messages"></div>
      <div class="idf-input-area">
        <textarea rows="1" placeholder="${this.config.placeholder}"></textarea>
        <button>Gönder</button>
      </div>
      <div class="idf-powered">Powered by ID Fine AI</div>
    `;
    this.shadow.appendChild(this.container);

    this.messagesEl = this.container.querySelector(".idf-messages")!;
    this.textarea = this.container.querySelector("textarea")!;
    this.sendBtn = this.container.querySelector(".idf-input-area button")!;
  }

  private bindEvents() {
    this.triggerBtn.addEventListener("click", () => this.toggle());

    this.container
      .querySelector(".idf-widget-fullscreen")!
      .addEventListener("click", () => this.toggleFullscreen());

    this.container
      .querySelector(".idf-widget-close")!
      .addEventListener("click", () => this.toggle());

    this.sendBtn.addEventListener("click", () => this.sendMessage());

    this.textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    // Auto-resize textarea
    this.textarea.addEventListener("input", () => {
      this.textarea.style.height = "auto";
      this.textarea.style.height =
        Math.min(this.textarea.scrollHeight, 100) + "px";
    });
  }

  private async initSession() {
    try {
      const initBody: Record<string, string> = {
        domain: location.hostname,
        page_url: location.href,
      };
      if (this.config.sourceGroupId) {
        initBody.source_group_id = this.config.sourceGroupId;
      }
      const response = await fetch(`${this.config.apiUrl}/api/widget/init`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(initBody),
      });
      const data = await response.json();
      this.visitorId = data.visitor_id;

      if (data.config?.welcome_message) {
        this.config.welcomeMessage = data.config.welcome_message;
      }

      // Add welcome message
      this.addMessage({
        id: "welcome",
        role: "assistant",
        content: this.config.welcomeMessage!,
        timestamp: new Date(),
      });

      // Connect WebSocket
      this.connectWS();
    } catch (e) {
      console.error("[idfine] Session init failed:", e);
    }
  }

  private connectWS() {
    this.ws = new WebSocketClient(
      this.config.apiUrl,
      this.visitorId,
      (data) => this.handleWSMessage(data),
      () => {
        const statusEl = this.container.querySelector(".idf-status");
        if (statusEl) statusEl.textContent = "Çevrimiçi";
      },
      () => {
        const statusEl = this.container.querySelector(".idf-status");
        if (statusEl) statusEl.textContent = "Bağlanıyor...";
      },
      this.config.sourceGroupId
    );
    this.ws.connect();
  }

  private handleWSMessage(data: WSIncoming) {
    switch (data.type) {
      case "stream_start":
        this.isStreaming = true;
        this.currentStreamId = data.message_id;
        this.currentStreamContent = "";
        this.removeTypingIndicator();
        this.addMessage({
          id: data.message_id,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
        });
        break;

      case "stream_chunk":
        this.currentStreamContent += data.content;
        this.updateStreamingMessage(
          data.message_id,
          this.currentStreamContent
        );
        break;

      case "stream_end":
        this.isStreaming = false;
        if (data.conversation_id) {
          this.conversationId = data.conversation_id;
        }
        this.finalizeMessage(data.message_id, data.sources, data.intent);
        this.sendBtn.disabled = false;
        this.textarea.disabled = false;
        this.textarea.focus();
        break;

      case "error":
        this.removeTypingIndicator();
        this.isStreaming = false;
        this.addMessage({
          id: Date.now().toString(),
          role: "assistant",
          content: data.message,
          timestamp: new Date(),
        });
        this.sendBtn.disabled = false;
        this.textarea.disabled = false;
        break;

      case "system":
        this.removeTypingIndicator();
        this.addMessage({
          id: Date.now().toString(),
          role: "system",
          content: data.content,
          timestamp: new Date(),
        });
        break;
    }
  }

  private sendMessage() {
    const content = this.textarea.value.trim();
    if (!content || this.isStreaming) return;

    // Add user message
    this.addMessage({
      id: Date.now().toString(),
      role: "user",
      content,
      timestamp: new Date(),
    });

    // Show typing indicator
    this.showTypingIndicator();

    // Disable input
    this.sendBtn.disabled = true;
    this.textarea.disabled = true;

    // Send via WebSocket
    if (this.ws?.isConnected) {
      this.ws.send({
        type: "message",
        content,
        conversation_id: this.conversationId || undefined,
      });
    } else {
      // Fallback to REST
      this.sendViaRest(content);
    }

    // Clear input
    this.textarea.value = "";
    this.textarea.style.height = "auto";
  }

  private async sendViaRest(content: string) {
    try {
      const response = await fetch(
        `${this.config.apiUrl}/api/widget/message`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Visitor-ID": this.visitorId,
          },
          body: JSON.stringify({
            content,
            conversation_id: this.conversationId || undefined,
          }),
        }
      );
      const data = await response.json();

      this.removeTypingIndicator();
      this.conversationId = data.conversation_id;

      // Create streaming-style bubble for simulated typing
      const msgId = data.message_id || Date.now().toString();
      this.addMessage({
        id: msgId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
        isStreaming: true,
      });

      const el = this.shadow.getElementById(`msg-${msgId}`);
      const fullText: string = data.content;

      if (el) {
        await new Promise<void>((resolve) => {
          let i = 0;
          const interval = setInterval(() => {
            const chunkSize = Math.floor(Math.random() * 6) + 3;
            i += chunkSize;
            if (i >= fullText.length) {
              el.classList.remove("streaming");
              el.innerHTML = renderMarkdown(fullText);
              clearInterval(interval);
              resolve();
            } else {
              el.textContent = fullText.substring(0, i);
              this.scrollToBottom();
            }
          }, 20);
        });
      }

      // Update stored message
      const msg = this.messages.find((m) => m.id === msgId);
      if (msg) {
        msg.content = fullText;
        msg.sources = data.sources;
        msg.intent = data.intent;
        msg.isStreaming = false;
      }
    } catch {
      this.removeTypingIndicator();
      this.addMessage({
        id: Date.now().toString(),
        role: "assistant",
        content: "Bağlantı hatası oluştu. Lütfen tekrar deneyin.",
        timestamp: new Date(),
      });
    } finally {
      this.sendBtn.disabled = false;
      this.textarea.disabled = false;
      this.textarea.focus();
    }
  }

  private addMessage(msg: ChatMessage) {
    this.messages.push(msg);

    if (msg.role === "assistant") {
      // Assistant: avatar + bubble in a row
      const row = document.createElement("div");
      row.className = "idf-msg-row";

      const avatarDiv = document.createElement("div");
      avatarDiv.className = "idf-msg-avatar";
      const avatarImg = document.createElement("img");
      avatarImg.src = this.logoUrl;
      avatarImg.alt = "AI";
      avatarDiv.appendChild(avatarImg);
      row.appendChild(avatarDiv);

      const el = document.createElement("div");
      el.className = "idf-message assistant";
      el.id = `msg-${msg.id}`;

      if (msg.isStreaming) {
        el.classList.add("streaming");
        el.textContent = msg.content;
      } else {
        el.innerHTML = renderMarkdown(msg.content);
      }

      row.appendChild(el);
      this.messagesEl.appendChild(row);
    } else if (msg.role === "user") {
      // User: right-aligned, no avatar
      const row = document.createElement("div");
      row.className = "idf-msg-row user-row";

      const el = document.createElement("div");
      el.className = "idf-message user";
      el.id = `msg-${msg.id}`;
      el.textContent = msg.content;

      row.appendChild(el);
      this.messagesEl.appendChild(row);
    } else if (msg.role === "system") {
      // System: centered info message
      const row = document.createElement("div");
      row.className = "idf-msg-row system-row";

      const el = document.createElement("div");
      el.className = "idf-message system";
      el.id = `msg-${msg.id}`;
      el.textContent = msg.content;

      row.appendChild(el);
      this.messagesEl.appendChild(row);
    }

    this.scrollToBottom();
  }

  private updateStreamingMessage(messageId: string, content: string) {
    const el = this.shadow.getElementById(`msg-${messageId}`);
    if (el) {
      el.textContent = content;
      this.scrollToBottom();
    }
  }

  private finalizeMessage(
    messageId: string,
    sources: Source[],
    intent: string
  ) {
    const msg = this.messages.find((m) => m.id === messageId);
    if (msg) {
      msg.content = this.currentStreamContent;
      msg.sources = sources;
      msg.intent = intent;
      msg.isStreaming = false;
    }

    const el = this.shadow.getElementById(`msg-${messageId}`);
    if (el) {
      el.classList.remove("streaming");
      el.innerHTML = renderMarkdown(this.currentStreamContent);

    }
  }

  private showTypingIndicator() {
    const existing = this.shadow.getElementById("idf-typing");
    if (existing) return;

    const row = document.createElement("div");
    row.className = "idf-msg-row";
    row.id = "idf-typing";

    const avatarDiv = document.createElement("div");
    avatarDiv.className = "idf-msg-avatar";
    const avatarImg = document.createElement("img");
    avatarImg.src = this.logoUrl;
    avatarImg.alt = "AI";
    avatarDiv.appendChild(avatarImg);
    row.appendChild(avatarDiv);

    const el = document.createElement("div");
    el.className = "idf-typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    row.appendChild(el);

    this.messagesEl.appendChild(row);
    this.scrollToBottom();
  }

  private removeTypingIndicator() {
    const el = this.shadow.getElementById("idf-typing");
    if (el) el.remove();
  }

  private scrollToBottom() {
    requestAnimationFrame(() => {
      this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    });
  }

  private toggleFullscreen() {
    this.isFullscreen = !this.isFullscreen;
    this.container.classList.toggle("fullscreen", this.isFullscreen);

    const expandIcon = this.container.querySelector(".idf-fs-expand") as HTMLElement;
    const shrinkIcon = this.container.querySelector(".idf-fs-shrink") as HTMLElement;
    if (expandIcon && shrinkIcon) {
      expandIcon.style.display = this.isFullscreen ? "none" : "block";
      shrinkIcon.style.display = this.isFullscreen ? "block" : "none";
    }

    this.scrollToBottom();
  }

  private toggle() {
    this.isOpen = !this.isOpen;
    this.container.classList.toggle("open", this.isOpen);
    this.triggerBtn.classList.toggle("open", this.isOpen);

    // Exit fullscreen when closing
    if (!this.isOpen && this.isFullscreen) {
      this.toggleFullscreen();
    }

    if (this.isOpen) {
      this.textarea.focus();
      this.scrollToBottom();
    }
  }

  destroy() {
    this.ws?.disconnect();
  }
}
