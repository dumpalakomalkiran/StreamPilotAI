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
  },
}));

describe("StreamPilot starter dashboard", () => {
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
