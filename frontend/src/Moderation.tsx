import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, ShieldAlert, X } from "lucide-react";
import { api, websocketUrl, type ModerationIncident } from "./api";

export default function Moderation() {
  const [incidents, setIncidents] = useState<ModerationIncident[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const refresh = useCallback(() => {
    api.listModerationIncidents().then(setIncidents).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Moderation flags are unavailable.");
    });
  }, []);

  useEffect(() => {
    refresh();
    let socket: WebSocket | undefined;
    try {
      socket = new WebSocket(`${websocketUrl}/ws/dashboard`);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => setConnected(false);
      socket.onerror = () => setConnected(false);
      socket.onmessage = (event) => {
        try {
          if (JSON.parse(event.data).type === "moderation.incident.created") refresh();
        } catch { /* Ignore malformed dashboard events; REST remains authoritative. */ }
      };
    } catch { setConnected(false); }
    return () => socket?.close();
  }, [refresh]);

  async function transition(incident: ModerationIncident, action: "dismiss" | "reviewed") {
    setBusyId(incident.id);
    setError("");
    try {
      const updated = action === "dismiss"
        ? await api.dismissModerationIncident(incident.id)
        : await api.reviewModerationIncident(incident.id);
      setIncidents((current) => current.filter((item) => item.id !== updated.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update this flag.");
    } finally { setBusyId(null); }
  }

  return <section className="moderation-view" aria-labelledby="moderation-title">
    <div className="moderation-safety-banner" role="note">
      <ShieldAlert size={20} aria-hidden="true" />
      <strong>Flags are suggestions. StreamPilot never bans, times out or deletes on its own.</strong>
    </div>
    <section className="panel moderation-panel">
      <div className="panel-heading">
        <div><div className="panel-title" id="moderation-title"><AlertTriangle size={17}/> Moderation review</div>
          <p>Review chat signals and choose what to do yourself.</p></div>
        <span className={`moderation-connection ${connected ? "connected" : ""}`} role="status">
          <i />{connected ? "Live updates" : "Reconnecting"}
        </span>
      </div>
      {error && <p role="alert" className="reply-error">{error}</p>}
      {incidents.length === 0 && !error && <div className="moderation-empty" role="status">No open flags to review.</div>}
      <div className="moderation-list">
        {incidents.map((incident) => <article className="moderation-card" key={incident.id}>
          <div className="moderation-card-top">
            <strong>{incident.user_display_name}</strong>
            <div className="moderation-badges">
              <span className={`moderation-category category-${incident.category}`}>{incident.category}</span>
              <span className={`moderation-severity severity-${incident.severity}`}>{incident.severity}</span>
            </div>
          </div>
          <p className="moderation-message">{incident.message}</p>
          <p className="moderation-reason">{incident.reason}</p>
          <div className="moderation-card-footer">
            <span>{Math.round(incident.confidence * 100)}% signal confidence · {new Date(incident.created_at).toLocaleTimeString()}</span>
            <div>
              <button className="button button-secondary" disabled={busyId === incident.id}
                onClick={() => transition(incident, "reviewed")}><Check size={14}/> Mark reviewed</button>
              <button className="button button-secondary" disabled={busyId === incident.id}
                onClick={() => transition(incident, "dismiss")}><X size={14}/> Dismiss</button>
            </div>
          </div>
        </article>)}
      </div>
    </section>
  </section>;
}
