import { useCallback, useEffect, useState } from "react";
import { Check, Clock3, Radio, X } from "lucide-react";
import { api, websocketUrl, type Challenge, type NativeTwitchPoll, type PollSuggestion } from "./api";

type Props = {
  onCountChange?: (count: number) => void;
  onStarted?: (challenge: Challenge) => void;
  onError?: (message: string) => void;
  nativePollsEnabled?: boolean;
};

export default function PollSuggestions({ onCountChange, onStarted, onError, nativePollsEnabled = false }: Props) {
  const [items, setItems] = useState<PollSuggestion[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [nativePolls, setNativePolls] = useState<NativeTwitchPoll[]>([]);

  const refresh = useCallback(async () => {
    try {
      const suggestions = await api.listPollSuggestions();
      setItems(suggestions);
      onCountChange?.(suggestions.length);
    } catch (error) {
      onError?.(error instanceof Error ? error.message : "Poll suggestions could not be loaded.");
    }
  }, [onCountChange, onError]);

  useEffect(() => {
    if (nativePollsEnabled) api.listNativeTwitchPolls().then(setNativePolls).catch(() => undefined);
  }, [nativePollsEnabled]);

  useEffect(() => {
    void refresh();
    let stopped = false;
    let timer = 0;
    let socket: WebSocket | null = null;
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(`${websocketUrl}/ws/dashboard`);
      socket.onopen = () => { void refresh(); };
      socket.onmessage = (event) => {
        try {
          if (JSON.parse(event.data).type === "poll.suggestion.created") void refresh();
        } catch { /* Ignore malformed socket frames; REST refresh is authoritative. */ }
      };
      socket.onclose = () => { if (!stopped) timer = window.setTimeout(connect, 1500); };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { stopped = true; window.clearTimeout(timer); socket?.close(); };
  }, [refresh]);

  function edited(item: PollSuggestion) {
    const title = (document.getElementById(`poll-title-${item.id}`) as HTMLInputElement | null)?.value ?? item.title;
    const options = item.options.map((_, index) =>
      (document.getElementById(`poll-option-${item.id}-${index}`) as HTMLInputElement | null)?.value.trim() ?? "",
    );
    const duration = Number((document.getElementById(`poll-duration-${item.id}`) as HTMLSelectElement | null)?.value ?? item.duration_seconds);
    return { title: title.trim(), options, duration_seconds: duration };
  }

  function removeSuggestion(item: PollSuggestion) {
    setItems((current) => current.filter((row) => row.id !== item.id));
    onCountChange?.(Math.max(0, items.length - 1));
  }

  async function approve(item: PollSuggestion) {
    setBusy(item.id);
    setErrors((current) => ({ ...current, [item.id]: "" }));
    try {
      const draft = await api.approvePollSuggestion(item.id, edited(item));
      await api.approveChallenge(draft.id);
      const launched = await api.launchChallenge(draft.id);
      removeSuggestion(item);
      onStarted?.(launched);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Poll could not be started.";
      setErrors((current) => ({ ...current, [item.id]: message }));
    } finally { setBusy(null); }
  }

  async function createNative(item: PollSuggestion) {
    setBusy(item.id);
    setErrors((current) => ({ ...current, [item.id]: "" }));
    let approvedDraft: Challenge | null = null;
    try {
      approvedDraft = await api.approvePollSuggestion(item.id, edited(item));
      const nativeDuration = Number((document.getElementById(`poll-native-duration-${item.id}`) as HTMLSelectElement | null)?.value ?? edited(item).duration_seconds);
      const poll = await api.createNativeTwitchPoll(item.id, nativeDuration);
      setNativePolls((current) => [poll, ...current.filter((entry) => entry.id !== poll.id)]);
      removeSuggestion(item);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Native Twitch poll could not be created.";
      if (message.includes("Affiliate or Partner") && message.includes("!vote N")) {
        try {
          if (!approvedDraft) throw new Error("The approved suggestion remains available; start it using StreamPilot voting.");
          await api.approveChallenge(approvedDraft.id);
          const launched = await api.launchChallenge(approvedDraft.id);
          removeSuggestion(item);
          onStarted?.(launched);
          onError?.("This channel is not Affiliate or Partner eligible for native polls. Started this approved suggestion with StreamPilot !vote N instead.");
          return;
        } catch (fallbackError) {
          setErrors((current) => ({ ...current, [item.id]: fallbackError instanceof Error ? fallbackError.message : message }));
          return;
        }
      }
      setErrors((current) => ({ ...current, [item.id]: message }));
    } finally { setBusy(null); }
  }

  async function dismiss(item: PollSuggestion) {
    setBusy(item.id);
    try {
      await api.dismissPollSuggestion(item.id);
      setItems((current) => current.filter((row) => row.id !== item.id));
      onCountChange?.(Math.max(0, items.length - 1));
    } catch (error) {
      setErrors((current) => ({ ...current, [item.id]: error instanceof Error ? error.message : "Poll suggestion could not be dismissed." }));
    } finally { setBusy(null); }
  }

  function changeOption(itemId: number, index: number, value: string) {
    setItems((current) => current.map((item) => item.id === itemId
      ? { ...item, options: item.options.map((option, optionIndex) => optionIndex === index ? value : option) }
      : item));
  }

  return <section className="panel poll-suggestions" aria-labelledby="poll-suggestions-title">
    <div className="panel-heading"><div><div className="panel-title" id="poll-suggestions-title"><Radio size={17}/> Poll suggestions</div><p>Viewer disagreement, ready for your review</p></div><span className="poll-live-label"><i/> AI drafts · streamer starts</span></div>
    {nativePolls.length > 0 && <div className="native-poll-list" aria-label="Tracked Twitch polls">{nativePolls.map((poll) => <article key={poll.id} className="native-poll-row"><strong>{poll.title}</strong><span>{poll.status}</span>{poll.status === "ACTIVE" && <button className="button button-secondary" onClick={() => api.endNativeTwitchPoll(poll.id).then((ended) => setNativePolls((rows) => rows.map((row) => row.id === ended.id ? ended : row))).catch((error:unknown) => onError?.(error instanceof Error ? error.message : "Could not end Twitch poll."))}>End poll</button>}</article>)}</div>}
    {items.length === 0 ? <div className="poll-empty" role="status">No competing preferences detected yet.</div> : <div className="poll-list">
      {items.map((item) => <article className="poll-card" key={item.id}>
        <label className="field-label" htmlFor={`poll-title-${item.id}`}>POLL TITLE</label>
        <input id={`poll-title-${item.id}`} className="text-input" maxLength={nativePollsEnabled ? 60 : 80} defaultValue={item.title}/>
        <div className="poll-options-heading"><span className="field-label">OPTIONS</span><span>{item.options.length} / 5</span></div>
        {item.options.map((option, index) => <div className="poll-option-editor" key={`${item.id}-${index}`}>
          <span>{index + 1}</span><input id={`poll-option-${item.id}-${index}`} aria-label={`Poll option ${index + 1}`} maxLength={25} value={option} onChange={(event) => changeOption(item.id, index, event.target.value)}/>
        </div>)}
        {item.options.length < 5 && <button className="poll-add-option" onClick={() => setItems((current) => current.map((row) => row.id === item.id ? { ...row, options: [...row.options, ""] } : row))}>Add option</button>}
        <label className="poll-duration" htmlFor={`poll-duration-${item.id}`}><Clock3 size={14}/> Duration<select id={`poll-duration-${item.id}`} defaultValue={item.duration_seconds}><option value={15}>15 sec</option><option value={30}>30 sec</option><option value={45}>45 sec</option><option value={60}>60 sec</option></select></label>
        <div className="poll-support"><strong>Supporting chat</strong>{item.supporting_messages.length
          ? item.supporting_messages.slice(0, 6).map((message, index) => <p key={`${item.id}-${index}`}><b>{message.display_name}</b>: {message.text}</p>)
          : <p>Supporting messages were removed or are no longer available.</p>}</div>
        {errors[item.id] && <p className="poll-error" role="alert">{errors[item.id]}</p>}
        <div className="poll-actions"><button className="button button-primary" disabled={busy === item.id} onClick={() => void approve(item)}><Check size={15}/>{busy === item.id ? "Starting…" : "Approve & start with !vote"}</button>{nativePollsEnabled && <><label className="poll-duration">Twitch poll duration<select id={`poll-native-duration-${item.id}`} defaultValue={item.duration_seconds}><option value={15}>15 sec</option><option value={30}>30 sec</option><option value={60}>1 min</option><option value={300}>5 min</option><option value={600}>10 min</option><option value={1800}>30 min</option></select></label><button className="button button-secondary" disabled={busy === item.id} onClick={() => void createNative(item)}><Radio size={14}/>{busy === item.id ? "Creating…" : "Approve & create Twitch poll"}</button></>}<button className="button button-secondary" disabled={busy === item.id} onClick={() => void dismiss(item)}><X size={14}/>Dismiss</button></div>
      </article>)}
    </div>}
  </section>;
}
