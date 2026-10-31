import { useEffect, useState } from "react";
import { websocketUrl } from "./api";

type OverlayChallenge = {
  title: string;
  question: string;
  status: string;
  source: string;
  total_votes: number;
  ends_at: string | null;
  winning_option_id: number | null;
  options: Array<{ id: number; label: string; votes: number }>;
};
type FeaturedQuestion = { canonical_text: string; kind: string; asker_count: number };

export default function Overlay() {
  const token = new URLSearchParams(window.location.search).get("token");
  const [challenge, setChallenge] = useState<OverlayChallenge | null>(null);
  const [featuredQuestion, setFeaturedQuestion] = useState<FeaturedQuestion | null>(null);
  const [connection, setConnection] = useState("Connecting…");
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!token) {
      setConnection("Overlay token is missing");
      return;
    }
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(`${websocketUrl}/ws/overlay`, ["streampilot-overlay", token]);
      socket.onopen = () => setConnection("Connected");
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as { type: string; status?: string; challenge?: OverlayChallenge | null };
        if (event.type === "challenge.started" || event.type === "vote.updated" || event.type === "challenge.completed") {
          setChallenge(event.challenge ?? null);
        } else if (event.type === "connection.status") {
          setConnection(event.status ?? "Connected");
          setChallenge(event.challenge ?? null);
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        setConnection("Reconnecting…");
        if (!stopped) retryTimer = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [token]);

  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let stopped = false;
    const connectQuestionSocket = () => {
      if (stopped) return;
      socket = new WebSocket(`${websocketUrl}/ws/overlay/question`, ["streampilot-overlay", token]);
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as { type: string; question?: FeaturedQuestion | null };
        if (event.type === "question.featured") setFeaturedQuestion(event.question ?? null);
        if (event.type === "question.hidden") setFeaturedQuestion(null);
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!stopped) retryTimer = window.setTimeout(connectQuestionSocket, 1500);
      };
    };
    connectQuestionSocket();
    return () => {
      stopped = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [token]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const total = challenge?.total_votes ?? 0;
  const secondsLeft = challenge?.status === "active" && challenge.ends_at
    ? Math.max(0, Math.ceil((Date.parse(challenge.ends_at) - now) / 1000))
    : null;
  return <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "transparent", color: "white", fontFamily: "Inter, system-ui, sans-serif" }}>
    <section aria-live="polite" style={{ width: "min(720px, 92vw)", padding: 28, borderRadius: 24, background: "rgba(15, 18, 31, .92)", border: "1px solid rgba(255,255,255,.15)", boxShadow: "0 20px 80px rgba(0,0,0,.35)" }}>
      <p style={{ margin: "0 0 8px", color: "#b7a2ff", letterSpacing: ".12em", fontSize: 12, fontWeight: 700 }}>STREAM PILOT · AUDIENCE VOTE</p>
      {challenge ? <>
        <h1 style={{ margin: "0 0 6px", fontSize: 32 }}>{challenge.title}</h1>
        <p style={{ margin: "0 0 20px", color: "#c7c9d4" }}>{challenge.question}</p>
        {challenge.options.map((option, index) => {
          const percent = total === 0 ? 0 : Math.round(option.votes / total * 100);
          return <div key={option.id} style={{ margin: "14px 0" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span>{index + 1}. {option.label}</span><strong>{percent}%</strong></div>
            <div style={{ height: 10, borderRadius: 20, background: "#343748", overflow: "hidden" }}><div style={{ width: `${percent}%`, height: "100%", background: index === 0 ? "#9c7bff" : "#7581a0", transition: "width .25s" }} /></div>
          </div>;
        })}
        <p style={{ margin: "18px 0 0", color: "#aeb2c3" }}>{total} votes · {secondsLeft !== null ? `Voting closes in ${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}` : "Voting closed"} {challenge.source === "demo" && "· Demo"}</p>
        {challenge.status === "completed" && <p role="status">{challenge.winning_option_id === null ? "The vote ended in a tie." : `Winner: ${challenge.options.find((option) => option.id === challenge.winning_option_id)?.label ?? "Selected option"}`}</p>}
      </> : <><h1 style={{ margin: 0, fontSize: 26 }}>Ready for the next challenge</h1><p style={{ color: "#c7c9d4" }}>The approved challenge and live vote will appear here.</p></>}
      {featuredQuestion && <aside className="overlay-question" aria-label="Question of the moment"><span>QUESTION OF THE MOMENT · {featuredQuestion.asker_count} VIEWERS</span><strong>{featuredQuestion.canonical_text}</strong></aside>}
      <small style={{ color: "#8990a4" }}>{connection}</small>
    </section>
  </main>;
}
