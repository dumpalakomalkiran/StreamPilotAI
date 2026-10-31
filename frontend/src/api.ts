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
  is_synthetic?: boolean;
}

export type InteractionStatus = "open" | "answered" | "snoozed" | "dismissed";
export type InteractionKind = "question" | "request" | "suggestion" | "other";
export interface InteractionGroup {
  id: number;
  kind: InteractionKind;
  canonical_text: string;
  summary: string;
  status: InteractionStatus;
  snoozed_until: string | null;
  priority_score: number;
  priority_components: { askers: number; recency: number; repetition: number };
  asker_count: number;
  message_count: number;
  first_seen_at: string;
  last_seen_at: string;
  answered_at: string | null;
}

export interface AIResponseSuggestion {
  id: number;
  group_id: number;
  text: string;
  model: string;
  created_at: string;
  status: "draft" | "edited" | "sent" | "discarded";
  sent_at: string | null;
  twitch_message_id: string | null;
}

export interface PollSuggestion {
  id: number;
  session_id: number;
  title: string;
  options: string[];
  duration_seconds: number;
  source_group_ids: number[];
  status: "suggested" | "approved" | "dismissed" | "expired";
  created_at: string;
  supporting_messages: Array<{ display_name: string; text: string; created_at: string }>;
}
export interface NativeTwitchPoll {
  id: number; suggestion_id: number; challenge_id: number; twitch_poll_id: string; title: string;
  options: string[]; duration_seconds: number; status: string; started_at: string; ended_at: string | null;
}
export interface NativePollSettings { enabled: boolean; reauthorization_required: boolean; message: string }

export interface AnalyticsSnapshot {
  session_id: number | null; total_messages: number; unique_chatters: number; questions_detected: number;
  groups_open: number; groups_answered: number; groups_dismissed: number;
  average_time_to_answer_seconds: number | null;
  top_open_groups: Array<{id:number; canonical_text:string; kind:string; status:string; asker_count:number; message_count:number; priority_score:number; last_seen_at:string}>;
  top_unanswered_questions: Array<{id:number; canonical_text:string; kind:string; status:string; asker_count:number; message_count:number; priority_score:number; last_seen_at:string}>;
  poll_suggestions_approved:number; poll_suggestions_dismissed:number;
  messages_per_minute:Array<{minute:string;count:number}>;
}
export interface SessionSummary {
  session_id:number; status:string; generated_at:string|null;
  content:null|{headline:string;top_topics:string[];frequently_asked_questions:Array<{question:string;status:string}>;unanswered_follow_ups:string[];audience_mood_observation:string;content_suggestions:string[]};
}

export interface ModerationIncident {
  id: number;
  session_id: number;
  message_id: string;
  user_display_name: string;
  message: string;
  category: "spam" | "repeated" | "link" | "harassment" | "other";
  severity: "low" | "medium" | "high";
  confidence: number;
  reason: string;
  status: "open" | "dismissed" | "reviewed";
  created_at: string;
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
  getCurrentAnalytics: () => request<AnalyticsSnapshot>("/api/analytics/current"),
  listModerationIncidents: (status = "open") => request<ModerationIncident[]>(`/api/moderation/incidents?status=${status}`),
  dismissModerationIncident: (id: number) => request<ModerationIncident>(`/api/moderation/incidents/${id}/dismiss`, { method: "POST" }),
  reviewModerationIncident: (id: number) => request<ModerationIncident>(`/api/moderation/incidents/${id}/reviewed`, { method: "POST" }),
  endCurrentSession: () => request<SessionSummary>("/api/analytics/current/end", { method: "POST" }),
  getSessionSummary: (id:number) => request<SessionSummary>(`/api/analytics/sessions/${id}/summary`),
  getLatestSessionSummary: () => request<SessionSummary>("/api/analytics/latest-summary"),
  generateSessionSummary: (id:number, regenerate=false) => request<SessionSummary>(`/api/analytics/sessions/${id}/summary`, {method:"POST",body:JSON.stringify({regenerate})}),
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
  listPollSuggestions: () => request<PollSuggestion[]>("/api/polls/suggestions"),
  approvePollSuggestion: (id: number, payload: { title: string; options: string[]; duration_seconds: number }) =>
    request<Challenge>(`/api/polls/suggestions/${id}/approve`, { method: "POST", body: JSON.stringify(payload) }),
  dismissPollSuggestion: (id: number) => request<PollSuggestion>(`/api/polls/suggestions/${id}/dismiss`, { method: "POST" }),
  getNativePollSettings: () => request<NativePollSettings>("/api/settings/native-polls"),
  saveNativePollSettings: (enabled: boolean) => request<NativePollSettings>("/api/settings/native-polls", { method: "PUT", body: JSON.stringify({ enabled }) }),
  listNativeTwitchPolls: () => request<NativeTwitchPoll[]>("/api/polls/twitch"),
  createNativeTwitchPoll: (suggestion_id: number, duration_seconds?: number) => request<NativeTwitchPoll>("/api/polls/twitch/create", { method: "POST", body: JSON.stringify({ suggestion_id, duration_seconds }) }),
  endNativeTwitchPoll: (id: number) => request<NativeTwitchPoll>(`/api/polls/twitch/${id}/end`, { method: "POST" }),
  listChatMessages: (limit = 50) => request<ChatMessage[]>(`/api/chat/messages?limit=${limit}`),
  listInteractions: (filters: { status?: string; kind?: string; q?: string } = {}) => {
    const query = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
    const encoded = query.toString();
    return request<InteractionGroup[]>(`/api/interactions${encoded ? `?${encoded}` : ""}`);
  },
  answerInteraction: (id: number) => request<InteractionGroup>(`/api/interactions/${id}/answer`, { method: "POST" }),
  dismissInteraction: (id: number) => request<InteractionGroup>(`/api/interactions/${id}/dismiss`, { method: "POST" }),
  snoozeInteraction: (id: number, minutes: number) => request<InteractionGroup>(`/api/interactions/${id}/snooze`, { method: "POST", body: JSON.stringify({ minutes }) }),
  featureInteraction: (id: number) => request<{ status: string; interaction_id: number }>(`/api/interactions/${id}/feature`, { method: "POST" }),
  suggestInteractionReply: (id: number) => request<AIResponseSuggestion>(`/api/interactions/${id}/response/suggest`, { method: "POST" }),
  editResponse: (id: number, text: string) => request<AIResponseSuggestion>(`/api/responses/${id}`, { method: "PATCH", body: JSON.stringify({ text }) }),
  sendResponse: (id: number) => request<AIResponseSuggestion>(`/api/responses/${id}/send`, { method: "POST" }),
  discardResponse: (id: number) => request<AIResponseSuggestion>(`/api/responses/${id}/discard`, { method: "POST" }),
  getStreamFacts: () => request<{ stream_facts: string }>("/api/settings/stream-facts"),
  saveStreamFacts: (stream_facts: string) => request<{ stream_facts: string }>("/api/settings/stream-facts", { method: "PUT", body: JSON.stringify({ stream_facts }) }),
};
