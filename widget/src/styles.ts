import type { WidgetConfig } from "./types";

export function getStyles(config: WidgetConfig): string {
  const brandColor = config.brandColor || "#231f20";
  const width = config.width || 380;
  const height = config.height || 560;
  const triggerSize = config.triggerSize || 60;
  const position = config.position || "bottom-right";
  const posRight = position === "bottom-right";

  return `
    :host {
      all: initial;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 14px;
      line-height: 1.5;
      color: #1a1a1a;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    /* ── Trigger Button ── */
    .idf-widget-trigger {
      position: fixed;
      bottom: 20px;
      ${posRight ? "right: 20px;" : "left: 20px;"}
      width: ${triggerSize}px;
      height: ${triggerSize}px;
      border-radius: 50%;
      background: transparent;
      border: none;
      cursor: pointer;
      padding: 0;
      z-index: 999999;
      transition: transform 0.2s;
    }

    .idf-widget-trigger:hover {
      transform: scale(1.08);
    }

    .idf-widget-trigger img {
      width: ${triggerSize}px;
      height: ${triggerSize}px;
      object-fit: contain;
      border-radius: 50%;
      filter: drop-shadow(0 4px 12px rgba(0,0,0,0.25));
    }

    .idf-widget-trigger.open img {
      display: none;
    }

    .idf-widget-trigger .idf-trigger-close {
      display: none;
      width: ${triggerSize}px;
      height: ${triggerSize}px;
      border-radius: 50%;
      background: ${brandColor};
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 12px rgba(0,0,0,0.25);
    }

    .idf-widget-trigger.open .idf-trigger-close {
      display: flex;
    }

    .idf-widget-trigger .idf-trigger-close svg {
      width: 24px;
      height: 24px;
      stroke: white;
      fill: none;
      stroke-width: 2;
    }

    /* ── Chat Container ── */
    .idf-widget-container {
      position: fixed;
      bottom: ${triggerSize + 30}px;
      ${posRight ? "right: 20px;" : "left: 20px;"}
      width: ${width}px;
      height: ${height}px;
      max-height: calc(100vh - ${triggerSize + 50}px);
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.15);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      z-index: 999999;
      opacity: 0;
      transform: translateY(20px) scale(0.95);
      transition: opacity 0.3s, transform 0.3s;
      pointer-events: none;
    }

    .idf-widget-container.open {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto;
    }

    /* ── Header ── */
    .idf-widget-header {
      background: ${brandColor};
      color: white;
      padding: 14px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }

    .idf-header-info {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .idf-header-logo {
      width: 32px;
      height: 32px;
      object-fit: contain;
      filter: brightness(0) invert(1);
    }

    .idf-header-text h3 {
      font-size: 15px;
      font-weight: 600;
      line-height: 1.2;
    }

    .idf-status {
      font-size: 11px;
      opacity: 0.8;
    }

    .idf-widget-close {
      background: none;
      border: none;
      color: white;
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .idf-widget-close:hover { background: rgba(255,255,255,0.15); }

    /* ── Messages Area ── */
    .idf-messages {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    /* ── Message Row (avatar + bubble) ── */
    .idf-msg-row {
      display: flex;
      align-items: flex-start;
      gap: 0;
    }

    .idf-msg-row.user-row {
      justify-content: flex-end;
    }

    .idf-msg-avatar {
      width: 32px;
      flex-shrink: 0;
      padding-right: 8px;
      margin-top: 2px;
    }

    .idf-msg-avatar img {
      width: 24px;
      height: 24px;
      object-fit: contain;
    }

    /* ── Message Bubble ── */
    .idf-message {
      max-width: 82%;
      padding: 10px 14px;
      border-radius: 12px;
      word-wrap: break-word;
      font-size: 14px;
      line-height: 1.5;
    }

    .idf-message.user {
      align-self: flex-end;
      background: ${brandColor};
      color: white;
      border-bottom-right-radius: 4px;
      white-space: pre-wrap;
    }

    .idf-message.assistant {
      align-self: flex-start;
      background: #f0f2f5;
      color: #1a202c;
      border-bottom-left-radius: 4px;
    }

    .idf-msg-row.system-row {
      justify-content: center;
    }

    .idf-message.system {
      background: #e2e8f0;
      color: #4a5568;
      font-size: 12px;
      font-style: italic;
      text-align: center;
      max-width: 90%;
      padding: 6px 14px;
      border-radius: 8px;
    }

    /* ── Streaming Cursor ── */
    .idf-message.assistant.streaming {
      white-space: pre-wrap;
    }

    .idf-message.assistant.streaming::after {
      content: '';
      display: inline-block;
      width: 2px;
      height: 1em;
      background: ${brandColor};
      margin-left: 2px;
      vertical-align: text-bottom;
      animation: idf-cursor-blink 0.7s steps(1) infinite;
    }

    @keyframes idf-cursor-blink {
      0%, 50% { opacity: 1; }
      51%, 100% { opacity: 0; }
    }

    /* ── Markdown Typography ── */
    .idf-message.assistant h3,
    .idf-message.assistant h4 {
      font-size: 14px;
      font-weight: 600;
      margin: 8px 0 4px;
    }

    .idf-message.assistant h3:first-child,
    .idf-message.assistant h4:first-child {
      margin-top: 0;
    }

    .idf-message.assistant strong { font-weight: 600; }
    .idf-message.assistant em { font-style: italic; }

    .idf-message.assistant ul,
    .idf-message.assistant ol {
      margin: 4px 0 4px 18px;
      padding: 0;
    }

    .idf-message.assistant li { margin-bottom: 2px; }

    .idf-message.assistant p { margin: 4px 0; }
    .idf-message.assistant p:first-child { margin-top: 0; }
    .idf-message.assistant p:last-child { margin-bottom: 0; }

    /* ── Sources ── */
    .idf-sources {
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid #ddd;
      font-size: 11px;
      color: #666;
    }

    /* ── Typing Indicator ── */
    .idf-typing {
      padding: 10px 14px;
      background: #f0f2f5;
      border-radius: 12px;
      border-bottom-left-radius: 4px;
      display: flex;
      gap: 4px;
      align-items: center;
    }

    .idf-typing span {
      width: 6px;
      height: 6px;
      background: #a0aec0;
      border-radius: 50%;
      animation: idf-bounce 1.4s ease-in-out infinite;
    }

    .idf-typing span:nth-child(2) { animation-delay: 0.2s; }
    .idf-typing span:nth-child(3) { animation-delay: 0.4s; }

    @keyframes idf-bounce {
      0%, 60%, 100% { transform: translateY(0); }
      30% { transform: translateY(-6px); }
    }

    /* ── Input Area ── */
    .idf-input-area {
      padding: 10px 14px;
      border-top: 1px solid #e5e5e5;
      display: flex;
      gap: 8px;
      align-items: flex-end;
      flex-shrink: 0;
    }

    .idf-input-area textarea {
      flex: 1;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 14px;
      font-family: inherit;
      resize: none;
      max-height: 100px;
      outline: none;
      transition: border-color 0.2s;
      line-height: 1.4;
    }

    .idf-input-area textarea:focus {
      border-color: ${brandColor};
    }

    .idf-input-area button {
      background: ${brandColor};
      color: white;
      border: none;
      border-radius: 8px;
      padding: 8px 14px;
      cursor: pointer;
      font-size: 14px;
      transition: opacity 0.2s;
      white-space: nowrap;
      flex-shrink: 0;
    }

    .idf-input-area button:hover { opacity: 0.9; }
    .idf-input-area button:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ── Footer ── */
    .idf-powered {
      text-align: center;
      padding: 6px;
      font-size: 10px;
      color: #999;
      background: #fafafa;
      flex-shrink: 0;
    }

    /* ── Mobile Responsive ── */
    @media (max-width: 480px) {
      .idf-widget-container {
        bottom: 0;
        right: 0;
        left: 0;
        width: 100%;
        height: 100%;
        max-height: 100vh;
        border-radius: 0;
      }

      .idf-widget-trigger {
        bottom: 16px;
        ${posRight ? "right: 16px;" : "left: 16px;"}
      }
    }
  `;
}
