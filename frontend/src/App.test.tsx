import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import App from "./App";
import { api } from "./api";

vi.mock("./api", () => ({
  api: {
    getStatus: vi.fn(() => Promise.resolve({
      demo_mode: true,
      gemini_configured: false,
      twitch_configured: false,
      twitch_authorized: false,
      integrations_verified: false,
    })),
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
    expect(screen.getByRole("status", { name: /live updates/i })).toBeInTheDocument();
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
});
