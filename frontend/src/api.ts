export interface ChallengeProposal {
  title: string;
  question: string;
  reason: string;
  duration_seconds: number;
  source: "gemini" | "fallback" | "demo";
  options: Array<{ label: string; description: string }>;
}

export interface Challenge extends ChallengeProposal {
  id: number;
  status: "draft" | "active" | "completed";
  approved: boolean;
  total_votes: number;
  winning_option_id: number | null;
  options: Array<{ id: number; label: string; description: string; position: number; votes: number }>;
}

export interface AppStatus {
  demo_mode: boolean;
  gemini_configured: boolean;
  twitch_configured: boolean;
  twitch_authorized: boolean;
  integrations_verified: boolean;
}

export interface ChatMessage {
  display_name: string;
  text: string;
  received_at: string;
}

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
export const websocketUrl = apiBase
  ? apiBase.replace(/^http/, "ws")
  : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new Error("Can't reach StreamPilot's backend. Start the backend and try again.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail ?? `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getStatus: () => request<AppStatus>("/api/status"),
  suggestChallenge: (chat_messages: string[]) =>
    request<ChallengeProposal>("/api/challenges/suggest", { method: "POST", body: JSON.stringify({ chat_messages }) }),
  createChallenge: (proposal: ChallengeProposal) =>
    request<Challenge>("/api/challenges", { method: "POST", body: JSON.stringify(proposal) }),
  approveChallenge: (id: number) =>
    request<Challenge>(`/api/challenges/${id}/approve`, { method: "POST" }),
  launchChallenge: (id: number) =>
    request<Challenge>(`/api/challenges/${id}/launch`, { method: "POST" }),
  endChallenge: (id: number) =>
    request<Challenge>(`/api/challenges/${id}/end`, { method: "POST" }),
  createOverlayToken: () =>
    request<{ token: string; url: string }>("/api/overlay/token", { method: "POST" }),
  listChallenges: () => request<Challenge[]>("/api/challenges"),
  listChatMessages: (limit = 50) => request<ChatMessage[]>(`/api/chat/messages?limit=${limit}`),
};
