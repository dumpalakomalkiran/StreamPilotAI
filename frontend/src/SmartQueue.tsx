import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Check, Clock3, MonitorUp, Radio, X } from "lucide-react";
import { api, websocketUrl, type InteractionGroup } from "./api";

type Props = { onError?: (message: string) => void };

export default function SmartQueue({ onError }: Props) {
  const [items, setItems] = useState<InteractionGroup[]>([]);
  const [connection, setConnection] = useState("Connecting");
  const [status, setStatus] = useState("open");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const socketRef = useRef<WebSocket | null>(null);
  const refresh = useCallback(async () => {
    try { setItems(await api.listInteractions({ status, kind, q: search.trim() })); }
    catch (error) { onError?.(error instanceof Error ? error.message : "Queue is unavailable."); }
  }, [status, kind, search, onError]);
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let delay = 1000;
    const open = () => {
      if (stopped) return;
      setConnection("Connecting");
      const socket = new WebSocket(`${websocketUrl}/ws/dashboard`);
      socketRef.current = socket;
      socket.onopen = () => { delay = 1000; setConnection("Live updates"); void refreshRef.current(); };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as { type?: string };
          if (["interaction.created", "interaction.updated", "session.started", "session.ended"].includes(event.type ?? "")) void refreshRef.current();
        } catch { /* Ignore malformed dashboard frames; the next REST refresh remains authoritative. */ }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        socketRef.current = null;
        if (stopped) return;
        setConnection("Reconnecting");
        timer = window.setTimeout(open, delay);
        delay = Math.min(delay * 2, 15000);
      };
    };
    open();
    return () => { stopped = true; window.clearTimeout(timer); socketRef.current?.close(); };
  }, []);

  async function action(id: number, fn: () => Promise<unknown>) {
    setBusy(id);
    try { await fn(); await refresh(); }
    catch (error) { onError?.(error instanceof Error ? error.message : "Queue action failed."); }
    finally { setBusy(null); }
  }

  function cardKeys(event: KeyboardEvent<HTMLElement>, item: InteractionGroup) {
    if (event.target !== event.currentTarget || item.status !== "open") return;
    const key = event.key.toLowerCase();
    if (key === "a") { event.preventDefault(); void action(item.id, () => api.answerInteraction(item.id)); }
    if (key === "s") { event.preventDefault(); void action(item.id, () => api.snoozeInteraction(item.id, 15)); }
    if (key === "d") { event.preventDefault(); void action(item.id, () => api.dismissInteraction(item.id)); }
  }

  return <section className="panel smart-queue" aria-labelledby="smart-queue-title">
    <div className="queue-heading"><div><div className="panel-title" id="smart-queue-title"><Radio size={17}/> Smart Queue</div><p>Repeated questions grouped by viewer intent</p></div><span className={`queue-connection ${connection === "Live updates" ? "connected" : ""}`} role="status"><i/> {connection}</span></div>
    <div className="queue-filters" aria-label="Queue filters">
      <label>Status<select aria-label="Filter status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option><option value="open">Open</option><option value="snoozed">Snoozed</option><option value="answered">Answered</option><option value="dismissed">Dismissed</option></select></label>
      <label>Kind<select aria-label="Filter kind" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All kinds</option><option value="question">Question</option><option value="request">Request</option><option value="suggestion">Suggestion</option><option value="other">Other</option></select></label>
      <label className="queue-search">Search<input aria-label="Search queue" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find a question…"/></label>
    </div>
    <div className="queue-list" aria-live="polite" aria-relevant="additions text">
      {items.length ? items.map((item) => <article className="queue-card" key={item.id} tabIndex={0} onKeyDown={(event) => cardKeys(event, item)} aria-label={`Queue card: ${item.canonical_text}`}>
        <div className="queue-card-top"><span className={`kind-badge kind-${item.kind}`}>{item.kind}</span><span className="priority-badge" title={`Priority ${item.priority_score.toFixed(1)} = askers ${item.priority_components.askers.toFixed(1)} + recency ${item.priority_components.recency.toFixed(1)} + repetition ${item.priority_components.repetition.toFixed(1)}`} aria-label={`Priority ${item.priority_score.toFixed(1)}. Askers ${item.priority_components.askers.toFixed(1)}, recency ${item.priority_components.recency.toFixed(1)}, repetition ${item.priority_components.repetition.toFixed(1)}`}>Priority {item.priority_score.toFixed(1)} <span>ⓘ</span></span></div>
        <h3>{item.canonical_text}</h3><div className="queue-card-meta"><span>{item.asker_count} {item.asker_count === 1 ? "viewer" : "viewers"} · {item.message_count} messages</span><span><Clock3 size={13}/> {timeSince(item.last_seen_at, now)}</span></div>
        {item.status === "open" && <div className="queue-actions">
          <button className="queue-answer" disabled={busy === item.id} onClick={() => void action(item.id, () => api.answerInteraction(item.id))}><Check size={14}/> Answer <kbd>A</kbd></button>
          <div className="snooze-wrap"><button disabled={busy === item.id} aria-label={`Snooze question ${item.id}`}><Clock3 size={14}/> Snooze</button><div className="snooze-menu" aria-label="Snooze duration">{[5, 15, 30].map((minutes) => <button key={minutes} onClick={() => void action(item.id, () => api.snoozeInteraction(item.id, minutes))}>{minutes} min</button>)}</div><kbd>S</kbd></div>
          <button disabled={busy === item.id} onClick={() => void action(item.id, () => api.dismissInteraction(item.id))}><X size={14}/> Dismiss <kbd>D</kbd></button>
          <button className="queue-feature" disabled={busy === item.id} onClick={() => void action(item.id, () => api.featureInteraction(item.id))}><MonitorUp size={14}/> Show on stream</button>
        </div>}
      </article>) : <div className="queue-empty"><span>✦</span><strong>Nothing in the queue yet</strong><p>Questions and requests from live chat will appear here, grouped by intent.</p></div>}
    </div>
    <p className="queue-shortcuts">Focus a card and press <kbd>A</kbd> answer, <kbd>S</kbd> snooze 15m, or <kbd>D</kbd> dismiss.</p>
  </section>;
}

function timeSince(value: string, now: number): string {
  const seconds = Math.max(0, Math.floor((now - Date.parse(value)) / 1000));
  if (!Number.isFinite(seconds)) return "just now";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
