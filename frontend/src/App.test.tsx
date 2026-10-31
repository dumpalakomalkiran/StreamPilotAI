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
    getLatestSessionSummary: vi.fn(() => Promise.reject(new Error("No recap"))),
    getSessionSummary: vi.fn(() => Promise.resolve({ session_id: 1, status: "pending", generated_at: null, content: null })),
    generateSessionSummary: vi.fn(),
    endCurrentSession: vi.fn(() => Promise.resolve({ session_id: 1, status: "pending", generated_at: null, content: null })),
    createChallenge: vi.fn(),
    approveChallenge: vi.fn(),
    launchChallenge: vi.fn(),
    endChallenge: vi.fn(),
    listChallenges: vi.fn(() => Promise.resolve([])),
    listPollSuggestions: vi.fn(() => Promise.resolve([])),
    approvePollSuggestion: vi.fn(),
    dismissPollSuggestion: vi.fn(() => Promise.resolve({})),
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
  });
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
    fireEvent.click(screen.getByRole("button", { name: /show on stream/i }));
    await waitFor(() => expect(api.featureInteraction).toHaveBeenCalledWith(41));
    const card = screen.getByRole("article", { name: /queue card/i });
    fireEvent.keyDown(card, { key: "a" });
    await waitFor(() => expect(api.answerInteraction).toHaveBeenCalledTimes(2));
    vi.unstubAllGlobals();
  });
  it("renders Live Studio and clearly labels demo mode", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Live Studio" })).toBeInTheDocument();
    expect(screen.getByText("DEMO MODE")).toBeInTheDocument();
    expect(screen.getByText("Twitch isn't connected yet")).toBeInTheDocument();
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
  it("edits and approves a poll through the existing challenge launch flow", async () => {
    const poll = {
      id: 17, session_id: 1, title: "Ranked or casual?", options: ["Ranked", "Casual"],
      duration_seconds: 30, source_group_ids: [2, 3], status: "suggested" as const,
      created_at: new Date().toISOString(), supporting_messages: [
        { display_name: "ViewerA", text: "Should we play ranked?", created_at: new Date().toISOString() },
        { display_name: "ViewerB", text: "Let's play casual!", created_at: new Date().toISOString() },
      ],
    };
    const launched = {
      id: 71, title: "What should we play?", question: "What should we play?", reason: "poll",
      duration_seconds: 45, source: "gemini" as const, status: "active" as const, approved: true,
      total_votes: 0, winning_option_id: null, options: [
        { id: 1, label: "Ranked", description: "", position: 1, votes: 0 },
        { id: 2, label: "Casual", description: "", position: 2, votes: 0 },
      ],
    };
    vi.mocked(api.listPollSuggestions).mockResolvedValue([poll]);
    vi.mocked(api.approvePollSuggestion).mockResolvedValue({ ...launched, status: "draft", approved: false });
    vi.mocked(api.approveChallenge).mockResolvedValue({ ...launched, status: "draft", approved: true });
    vi.mocked(api.launchChallenge).mockResolvedValue(launched);
    const sockets: MockSocket[] = [];
    class MockSocket {
      onopen: (() => void) | null = null; onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null; onclose: (() => void) | null = null;
      constructor() { sockets.push(this); } close() { this.onclose?.(); }
    }
    vi.stubGlobal("WebSocket", MockSocket);
    render(<App />);
    expect(await screen.findByDisplayValue("Ranked or casual?")).toBeInTheDocument();
    expect(screen.getByText("ViewerA")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("POLL TITLE"), { target: { value: "What should we play?" } });
    fireEvent.change(screen.getByLabelText("Poll option 2"), { target: { value: "Casual co-op" } });
    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: /approve & start/i }));
    await waitFor(() => expect(api.launchChallenge).toHaveBeenCalledWith(71));
    expect(api.approvePollSuggestion).toHaveBeenCalledWith(17, {
      title: "What should we play?", options: ["Ranked", "Casual co-op"], duration_seconds: 45,
    });
    expect(api.approveChallenge).toHaveBeenCalledWith(71);
    expect(await screen.findByText(/Challenge launched through the backend/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
  it("dismisses poll suggestions and shows the queue badge count", async () => {
    const poll = {
      id: 18, session_id: 1, title: "Ranked or casual?", options: ["Ranked", "Casual"],
      duration_seconds: 30, source_group_ids: [], status: "suggested" as const,
      created_at: new Date().toISOString(), supporting_messages: [],
    };
    vi.mocked(api.listPollSuggestions).mockResolvedValue([poll]);
    const sockets: MockSocket[] = [];
    class MockSocket {
      onopen: (() => void) | null = null; onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null; onclose: (() => void) | null = null;
      constructor() { sockets.push(this); } close() { this.onclose?.(); }
    }
    vi.stubGlobal("WebSocket", MockSocket);
    render(<App />);
    expect(await screen.findByLabelText("1 poll suggestions")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(api.dismissPollSuggestion).toHaveBeenCalledWith(18));
    vi.unstubAllGlobals();
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
});
