export interface WidgetConfig {
  apiUrl: string;
  sourceGroupId?: string;
  position?: "bottom-right" | "bottom-left";
  brandColor?: string;
  brandName?: string;
  welcomeMessage?: string;
  placeholder?: string;
  logoUrl?: string;
  logoVariant?: "dark" | "light";
  width?: number;
  height?: number;
  triggerSize?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  sources?: Source[];
  intent?: string;
  timestamp: Date;
  isStreaming?: boolean;
}

export interface Source {
  document: string;
  chunk_index: number;
  score: number;
}

export interface WSMessage {
  type: "message" | "ping" | "typing";
  content?: string;
  conversation_id?: string;
}

export interface WSStreamStart {
  type: "stream_start";
  message_id: string;
}

export interface WSStreamChunk {
  type: "stream_chunk";
  content: string;
  message_id: string;
}

export interface WSStreamEnd {
  type: "stream_end";
  message_id: string;
  conversation_id?: string;
  sources: Source[];
  intent: string;
}

export interface WSError {
  type: "error";
  message: string;
}

export interface WSSystem {
  type: "system";
  content: string;
  event?: "escalated" | "agent_joined" | "agent_left";
}

export type WSIncoming = WSStreamStart | WSStreamChunk | WSStreamEnd | WSError | WSSystem;
