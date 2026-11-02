import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import App from "./App";
import { api } from "./api";

vi.mock("./api", () => ({
  websocketUrl: "ws://localhost",
  api: {
    getStatus: vi.fn(() => Promise.resolve({
      demo_mode: true,
      gemini_configured: false,
      twitch_configured: false,
      twitch_authorized: false,
      integrations_verified: false,
    })),
    getCurrentAnalytics: vi.fn(() => Promise.resolve({ session_id: null, total_messages: 0, unique_chatters: 0, questions_detected: 0, groups_open: 0, groups_answered: 0, groups_dismissed: 0, average_time_to_answer_seconds: null, top_open_groups: [], top_unanswered_questions: [], poll_suggestions_approved: 0, poll_suggestions_dismissed: 0, messages_per_minute: [] })),
    listModerationIncidents: vi.fn(() => Promise.resolve([])),
    dismissModerationIncident: vi.fn(() => Promise.resolve({ id: 71, status: "dismissed" })),
    reviewModerationIncident: vi.fn(() => Promise.resolve({ id: 71, status: "reviewed" })),
    getLatestSessionSummary: vi.fn(() => Promise.reject(new Error("No recap"))),
    getSessionSummary: vi.fn(() => Promise.resolve({ session_id: 1, status: "pending", generated_at: null, content: null })),
    generateSessionSummary: vi.fn(),
    endCurrentSession: vi.fn(() => Promise.resolve({ session_id: 1, status: "pending", generated_at: null, content: null })),
    createChallenge: vi.fn(),
    approveChallenge: vi.fn(),
    launchChallenge: vi.fn(),
    endChallenge: vi.fn(),
    listChallenges: vi.fn(() => Promise.resolve([])),
    listChatMessages: vi.fn(() => Promise.resolve([])),
    listInteractions: vi.fn(() => Promise.resolve([{
      id: 41, kind: "question", canonical_text: "Which GPU are you using?", summary: "GPU question",
      status: "open", snoozed_until: null, priority_score: 9.4,
      priority_components: { askers: 5.5, recency: 3, repetition: 0.9 },
      asker_count: 4, message_count: 6, first_seen_at: new Date().toISOString(),
      last_seen_at: new Date().toISOString(), answered_at: null,
    }])),
    answerInteraction: vi.fn(() => Promise.resolve({})),
    dismissInteraction: vi.fn(() => Promise.resolve({})),
    snoozeInteraction: vi.fn(() => Promise.resolve({})),
    featureInteraction: vi.fn(() => Promise.resolve({ status: "featured", interaction_id: 41 })),
    suggestInteractionReply: vi.fn(() => Promise.resolve({ id: 91, group_id: 41, text: "I use an RTX 4070!", model: "mock-model", created_at: new Date().toISOString(), status: "draft", sent_at: null, twitch_message_id: null })),
    editResponse: vi.fn((_id: number, text: string) => Promise.resolve({ id: 91, group_id: 41, text, model: "mock-model", created_at: new Date().toISOString(), status: "edited", sent_at: null, twitch_message_id: null })),
    sendResponse: vi.fn(() => Promise.resolve({ id: 91, group_id: 41, text: "I use an RTX 4070!", model: "mock-model", created_at: new Date().toISOString(), status: "sent", sent_at: new Date().toISOString(), twitch_message_id: "msg-1" })),
    discardResponse: vi.fn(() => Promise.resolve({})),
    getStreamFacts: vi.fn(() => Promise.resolve({ stream_facts: "" })),
    saveStreamFacts: vi.fn((stream_facts: string) => Promise.resolve({ stream_facts })),
  },
}));

describe("StreamPilot starter dashboard", () => {
  it("keeps AI replies as drafts until the streamer explicitly sends them", async () => {
    const sockets: MockSocket[] = [];
    class MockSocket {
      onopen: (() => void) | null = null; onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null; onclose: (() => void) | null = null;
      constructor() { sockets.push(this); } close() { this.onclose?.(); }
    }
    vi.stubGlobal("WebSocket", MockSocket);
    render(<App />);
    expect(await screen.findByText("Which GPU are you using?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /suggest reply/i }));
    expect(await screen.findByDisplayValue("I use an RTX 4070!")).toBeInTheDocument();
    expect(api.sendResponse).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/AI reply draft/i), { target: { value: "Edited reply" } });
    fireEvent.click(screen.getByRole("button", { name: /send to chat/i }));
    await waitFor(() => expect(api.sendResponse).toHaveBeenCalledWith(91));
    expect(api.editResponse).toHaveBeenCalledWith(91, "Edited reply");
    expect(await screen.findByText(/Answered · reply sent to chat/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  }, 15_000);
  it("renders the live queue and executes card actions", async () => {
    const sockets: MockSocket[] = [];
    class MockSocket {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: (() => void) | null = null;
      constructor() { sockets.push(this); }
      close() { this.onclose?.(); }
    }
    vi.stubGlobal("WebSocket", MockSocket);
    render(<App />);
    expect(await screen.findByText("Which GPU are you using?")).toBeInTheDocument();
    sockets[0]?.onopen?.();
    await waitFor(() => expect(screen.getByText("Live updates")).toBeInTheDocument());
    sockets[0]?.onmessage?.({ data: JSON.stringify({ type: "interaction.updated" }) });
    expect(api.listInteractions).toHaveBeenCalled();
    expect(screen.getByText("4 viewers · 6 messages")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /answer/i }));
    await waitFor(() => expect(api.answerInteraction).toHaveBeenCalledWith(41));
    fireEvent.keyDown(screen.getByRole("article", { name: /queue card/i }), { key: "s" });
    await waitFor(() => expect(api.snoozeInteraction).toHaveBeenCalledWith(41, 15));
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => expect(api.dismissInteraction).toHaveBeenCalledWith(41));
    const card = screen.getByRole("article", { name: /queue card/i });
    fireEvent.keyDown(card, { key: "a" });
    await waitFor(() => expect(api.answerInteraction).toHaveBeenCalledTimes(2));
    vi.unstubAllGlobals();
  }, 15_000);
  it("renders Live Studio and clearly labels demo mode", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Live Studio" })).toBeInTheDocument();
    expect(screen.getByText("DEMO MODE")).toBeInTheDocument();
    expect(screen.getByText("Twitch isn't connected yet")).toBeInTheDocument();
  });
  it("confirms session end and offers Twitch reconnect after success", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /end session/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("dialog").querySelector(".button-primary") as HTMLElement);
    await waitFor(() => expect(api.endCurrentSession).toHaveBeenCalled());
    expect(await screen.findByText(/Session ended successfully/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /connect twitch/i })).toHaveAttribute("href", "/auth/twitch");
  });
  it("does not show End session when live authorization has no active session", async () => {
    vi.mocked(api.getStatus).mockResolvedValueOnce({
      demo_mode: false, gemini_configured: true, twitch_configured: true,
      twitch_authorized: true, integrations_verified: false,
    });
    render(<App />);
    await waitFor(() => expect(screen.getByRole("link", { name: /connect twitch/i })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /end session/i })).not.toBeInTheDocument();
  });
  it("sends the reviewed challenge through create, approve, and launch", async () => {
    vi.mocked(api.createChallenge).mockResolvedValue({ id: 7 } as never);
    vi.mocked(api.approveChallenge).mockResolvedValue({} as never);
    vi.mocked(api.launchChallenge).mockResolvedValue({} as never);
    render(<App />);
    fireEvent.change(screen.getByLabelText("Voting option 1"), { target: { value: "Choose the cave" } });
    fireEvent.change(screen.getByLabelText("Challenge duration in seconds"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: /approve & launch/i }));
    await waitFor(() => expect(screen.getByText(/Challenge launched through the backend/i)).toBeInTheDocument());
    expect(api.createChallenge).toHaveBeenCalledWith(expect.objectContaining({
      duration_seconds: 45,
      options: expect.arrayContaining([expect.objectContaining({ label: "Choose the cave" })]),
    }));
    expect(api.approveChallenge).toHaveBeenCalledWith(7);
    expect(api.launchChallenge).toHaveBeenCalledWith(7);
  });
  it("keeps focus while editing a challenge voting option", () => {
    render(<App />);
    const option = screen.getByLabelText("Voting option 1");
    option.focus();
    fireEvent.change(option, { target: { value: "Choose the cave" } });
    expect(document.activeElement).toBe(option);
  });
  it("restores persisted vote results after a page reload", async () => {
    vi.mocked(api.listChallenges).mockResolvedValueOnce([{
      id: 19, session_id: 3, title: "Choose a route", question: "Which route?",
      reason: "Viewer vote", duration_seconds: 30, source: "demo", status: "completed",
      approved: true, total_votes: 7, winning_option_id: 2,
      options: [
        { id: 101, label: "Cave", description: "", position: 1, votes: 2 },
        { id: 102, label: "Bridge", description: "", position: 2, votes: 5 },
      ],
    }]);
    render(<App />);
    expect(await screen.findByText("Final results from the existing vote records")).toBeInTheDocument();
    expect(screen.getByText("7 backend votes")).toBeInTheDocument();
    expect(screen.getByText("Bridge")).toBeInTheDocument();
  });
  it("renders the Insights analytics view", async () => {
    vi.mocked(api.getCurrentAnalytics).mockResolvedValue({
      session_id: 4, total_messages: 12, unique_chatters: 5, questions_detected: 3,
      groups_open: 2, groups_answered: 1, groups_dismissed: 0,
      average_time_to_answer_seconds: 18,
      top_open_groups: [], top_unanswered_questions: [{ id: 8, canonical_text: "What GPU?", kind: "question", status: "open", asker_count: 3, message_count: 4, priority_score: 5, last_seen_at: new Date().toISOString() }],
      poll_suggestions_approved: 0, poll_suggestions_dismissed: 0,
      messages_per_minute: [{ minute: new Date().toISOString(), count: 2 }],
    });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Insights" }));
    expect(await screen.findByText("Messages per minute")).toBeInTheDocument();
    expect(screen.getByText("What GPU?")).toBeInTheDocument();
    expect(screen.getByText("Top unanswered questions")).toBeInTheDocument();
  });

  it("keeps native poll controls out of the dashboard", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(await screen.findByText("Integration settings")).toBeInTheDocument();
    expect(screen.queryByText("Native Twitch polls")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Live Studio" }));
    expect(screen.queryByRole("button", { name: /approve & create twitch poll/i })).not.toBeInTheDocument();
  });

  it("shows conservative moderation flags and lets the streamer dismiss one", async () => {
    vi.mocked(api.listModerationIncidents).mockResolvedValueOnce([{
      id: 71, session_id: 4, message_id: "message-71", user_display_name: "NewViewer",
      message: "visit https://example.org", category: "link", severity: "medium",
      confidence: 0.9, reason: "Contains a link from an account with no earlier chat in this session.",
      status: "open", created_at: new Date().toISOString(),
    }]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /moderation/i }));
    expect(await screen.findByText("Flags are suggestions. StreamPilot never bans, times out or deletes on its own.")).toBeInTheDocument();
    expect(await screen.findByText("visit https://example.org")).toBeInTheDocument();
    expect(screen.getByText("link", { selector: ".moderation-category" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => expect(api.dismissModerationIncident).toHaveBeenCalledWith(71));
    expect(screen.queryByText("visit https://example.org")).not.toBeInTheDocument();
  });
});
