import { ChatWidget } from "./ChatWidget";
import type { WidgetConfig } from "./types";

declare global {
  interface Window {
    IdfineChat?: {
      init: (config: WidgetConfig) => ChatWidget;
      _instance?: ChatWidget;
    };
  }
}

function init(config: WidgetConfig): ChatWidget {
  if (!config.apiUrl) {
    throw new Error("[idfine] apiUrl is required");
  }

  // Create host element
  const host = document.createElement("div");
  host.id = "idfine-chat-widget";
  document.body.appendChild(host);

  const widget = new ChatWidget(host, config);
  window.IdfineChat!._instance = widget;

  return widget;
}

// Auto-init from script tag data attributes
function autoInit() {
  const script = document.currentScript as HTMLScriptElement | null;
  if (!script) return;

  const apiUrl = script.getAttribute("data-api-url");
  if (apiUrl) {
    init({
      apiUrl,
      sourceGroupId: script.getAttribute("data-source-group") || undefined,
      brandColor: script.getAttribute("data-brand-color") || "#231f20",
      brandName: script.getAttribute("data-brand-name") || "idfine",
      position:
        (script.getAttribute("data-position") as WidgetConfig["position"]) ||
        "bottom-right",
      logoUrl: script.getAttribute("data-logo-url") || undefined,
      logoVariant:
        (script.getAttribute("data-logo-variant") as WidgetConfig["logoVariant"]) ||
        undefined,
      width: Number(script.getAttribute("data-width")) || undefined,
      height: Number(script.getAttribute("data-height")) || undefined,
      triggerSize: Number(script.getAttribute("data-trigger-size")) || undefined,
    });
  }
}

// Expose global API
window.IdfineChat = { init };

// Auto-init when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", autoInit);
} else {
  autoInit();
}

export { ChatWidget, init };
export type { WidgetConfig, ChatMessage } from "./types";
