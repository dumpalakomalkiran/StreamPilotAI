import { useEffect, useRef, useState } from "react";
import { Activity, Bot, Check, ChevronRight, Gamepad2, Gauge, LayoutDashboard, MessageCircle, MonitorPlay, Radio, Settings, ShieldAlert, ShieldCheck, Sparkles, Trophy, Users } from "lucide-react";
import { api, type AnalyticsSnapshot, type AppStatus, type Challenge, type ChallengeProposal, type ChatMessage } from "./api";
import Overlay from "./Overlay";
import SmartQueue from "./SmartQueue";
import Insights from "./Insights";
import Moderation from "./Moderation";
import "./chat.css";
import "./smart-queue.css";
import "./moderation.css";

export default function App() {
  return window.location.pathname === "/overlay" ? <Overlay /> : <Dashboard />;
}

function Dashboard() {
  const sampleChat = [
    { display_name: "Maya", text: "Should you open the mystery door?" },
    { display_name: "Ravi", text: "NOOOO 😂 take the safe route!" },
    { display_name: "Jules", text: "Vote YES! We need to see what's inside." },
    { display_name: "Tom", text: "Chat decides. Left door!" },
    { display_name: "Ari", text: "Make it a challenge!" },
  ];
  const [activeNav, setActiveNav] = useState("Live Studio");
  const [launched, setLaunched] = useState(false);
  const [challengeId, setChallengeId] = useState<number | null>(null);
  const [currentChallenge, setCurrentChallenge] = useState<Challenge | null>(null);
  const [currentChallengeIsPoll, setCurrentChallengeIsPoll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState("");
  const [title, setTitle] = useState("The Mystery Door Decision");
  const [question, setQuestion] = useState("Which route should the player take?");
  const [options, setOptions] = useState(["Open the mystery door", "Walk away safely"]);
  const [challengeSource, setChallengeSource] = useState<ChallengeProposal["source"]>("demo");
  const [duration, setDuration] = useState(30);
  const [suggesting, setSuggesting] = useState(false);
  const [overlayUrl, setOverlayUrl] = useState("");
  const [appStatus, setAppStatus] = useState<AppStatus>({ demo_mode: true, gemini_configured: false, twitch_configured: false, twitch_authorized: false, integrations_verified: false });
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSnapshot | null>(null);
  const [chatFeedError, setChatFeedError] = useState("");
  const [votes, setVotes] = useState([65, 35]);
  const [summarySessionId, setSummarySessionId] = useState<number | null>(null);
  const [sessionEnded, setSessionEnded] = useState(false);
  const [sessionMessage, setSessionMessage] = useState("");
  const [showEndSessionModal, setShowEndSessionModal] = useState(false);
  const chatListRef = useRef<HTMLDivElement>(null);
  const nav = [
    { label: "Live Studio", icon: MonitorPlay },
    { label: "Activity", icon: Activity },
    { label: "Insights", icon: Gauge },
    { label: "Moderation", icon: ShieldAlert },
    { label: "Settings", icon: Settings },
  ];
  const total = votes[0] + votes[1];
  const showVotePreview = appStatus.demo_mode && !currentChallenge;
  const hasActiveSession = appStatus.demo_mode
    ? !sessionEnded
    : analytics?.session_id != null && !sessionEnded;
  const voteOptions: Array<{ label: string; votes?: number; id?: number }> = currentChallenge?.options ?? [
    { label: "Open the mystery door" }, { label: "Walk away safely" },
  ];
  useEffect(() => {
    let active = true;
    api.getStatus().then((status) => {
      if (!active) return;
      setAppStatus(status);
      setChatMessages(status.demo_mode ? sampleChat.map((message) => ({ ...message, received_at: "" })) : []);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!appStatus.twitch_authorized) return;
    let active = true;
    const refreshChat = () => api.listChatMessages(50).then((messages) => {
      if (active) {
        setChatMessages(messages);
        setChatFeedError("");
      }
    }).catch((error: unknown) => {
      if (active) {
        setChatMessages([]);
        setChatFeedError(error instanceof Error ? error.message : "Live chat is unavailable.");
      }
    });
    void refreshChat();
    const timer = window.setInterval(refreshChat, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [appStatus.twitch_authorized]);
  useEffect(() => {
    if (!appStatus.demo_mode && !appStatus.twitch_authorized) return;
    let active = true;
    api.listChallenges().then((items) => {
      if (!active || items.length === 0) return;
      // Restore the most useful persisted state after a reload: an active
      // challenge takes precedence, otherwise keep the newest completed
      // result visible instead of falling back to the sample preview.
      const restored = items.find((item) => item.status === "active")
        ?? items.find((item) => item.status === "completed")
        ?? items[0];
      setChallengeId(restored.id);
      setCurrentChallenge(restored);
      setLaunched(restored.status === "active");
      setVotes(restored.options.map((option) => option.votes));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [appStatus.demo_mode, appStatus.twitch_authorized]);
  useEffect(() => {
    if (!appStatus.twitch_authorized) return;
    let active = true;
    const refreshAnalytics = () => api.getCurrentAnalytics().then((snapshot) => {
      if (active) setAnalytics(snapshot);
    }).catch(() => undefined);
    void refreshAnalytics();
    const timer = window.setInterval(refreshAnalytics, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [appStatus.twitch_authorized]);
  useEffect(() => {
    const list = chatListRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [chatMessages]);
  useEffect(() => {
    if (challengeId === null) return;
    let active = true;
    const refreshChallenge = () => api.listChallenges().then((items) => {
      const found = items.find((item) => item.id === challengeId);
      if (active && found) {
        setCurrentChallenge(found);
        setLaunched(found.status === "active");
        setVotes(found.options.map((option) => option.votes));
      }
    }).catch(() => undefined);
    void refreshChallenge();
    const timer = window.setInterval(refreshChallenge, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [challengeId]);
  async function approveAndLaunch() {
    setBusy(true);
    setApiError("");
    try {
      const created = await api.createChallenge({
        title,
        question,
        reason: "Streamer-approved demo challenge.",
        duration_seconds: duration,
        source: challengeSource,
        options: [
          ...options.map((label) => ({ label, description: "Community-selected option" })),
        ],
      });
      await api.approveChallenge(created.id);
      const launchedChallenge = await api.launchChallenge(created.id);
      setChallengeId(created.id);
      setCurrentChallenge(launchedChallenge);
      setLaunched(true);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Challenge could not be launched.");
    } finally {
      setBusy(false);
    }
  }
  async function endSession() {
    setShowEndSessionModal(true);
  }
  async function confirmEndSession() {
    setShowEndSessionModal(false);
    if (challengeId !== null) {
      try {
        const completed = await api.endChallenge(challengeId);
        setCurrentChallenge(completed);
      } catch (error) {
        setApiError(error instanceof Error ? error.message : "Session could not be ended.");
        return;
      }
    }
    try {
      const result = await api.endCurrentSession();
      setSummarySessionId(result.session_id);
    } catch (error) {
      if (!(error instanceof Error) || !error.message.includes("No active session")) {
        setApiError(error instanceof Error ? error.message : "Session could not be ended.");
        return;
      }
    }
    setLaunched(false);
    setChallengeId(null);
    setCurrentChallenge(null);
    setSessionEnded(true);
    setSessionMessage("Session ended successfully. Reconnect Twitch when you are ready to start a new session.");
  }
  async function requestSuggestion() {
    setSuggesting(true);
    setApiError("");
    try {
      const context = appStatus.twitch_authorized
        ? (await api.listChatMessages(50)).map((message) => message.text)
        : sampleChat.map((message) => message.text);
      const proposal = await api.suggestChallenge(context);
      setTitle(proposal.title);
      setQuestion(proposal.question);
      setOptions(proposal.options.map((option) => option.label));
      setDuration(proposal.duration_seconds);
      setChallengeSource(proposal.source);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Suggestion could not be generated.");
    } finally {
      setSuggesting(false);
    }
  }
  async function generateOverlayLink() {
    setApiError("");
    try {
      const result = await api.createOverlayToken();
      setOverlayUrl(result.url);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Overlay link could not be created.");
    }
  }
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={21}/></div><div><strong>StreamPilot</strong><span>AI CREATOR STUDIO</span></div></div>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(({label,icon:Icon})=><button key={label} className={`nav-item ${activeNav===label?"active":""}`} onClick={()=>setActiveNav(label)}><Icon size={18}/><span>{label}</span>{label==="Live Studio"&&<i className="live-dot"/>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="profile"><div className="avatar">SP</div><div><strong>Streamer workspace</strong><span>{appStatus.demo_mode?"Demo environment":"Live environment"}</span></div></div></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14}/><strong>{activeNav}</strong></div><div className="top-actions"><span className="demo-pill"><span/> {appStatus.demo_mode?"DEMO MODE":"LIVE MODE"}</span></div></header>
      <div className="content">
        <section className="welcome-row"><div><div className="eyebrow"><Gamepad2 size={15}/> YOUR STREAM, MORE INTERACTIVE</div><h1>{activeNav}</h1><p className="subheading">Turn your community's energy into the next big moment.</p>{sessionMessage&&<p className="session-success" role="status"><Check size={14}/>{sessionMessage}</p>}</div>{hasActiveSession?<button className="button button-secondary" onClick={endSession}><Activity size={16}/> End session</button>:<a className="button button-primary" href="/auth/twitch"><Radio size={16}/> Connect Twitch</a>}</section>
        {apiError&&<div role="alert" className="inline-alert"><strong>Request needs attention</strong><span>{apiError}</span></div>}
        {activeNav === "Live Studio" ? <>
        <section className="connection-banner"><div className="connection-icon"><Radio size={20}/></div><div className="connection-copy"><strong>{appStatus.twitch_authorized?"Twitch account authorized":"Twitch isn't connected yet"}</strong><span>{appStatus.twitch_authorized?(analytics?.session_id != null ? "Active StreamPilot session is ready for live controls.":"Authorization is saved, but no active session is running."):"This is a local preview. Connect Twitch to receive live chat."}</span></div>{appStatus.twitch_authorized?<span className="status-chip"><span/> {analytics?.session_id != null?"Session active":"Authorized"}</span>:appStatus.twitch_configured?<a className="button button-secondary" href="/auth/twitch">Connect Twitch</a>:<span className="status-chip"><span/> Not connected</span>}</section>
        <section className="stats-grid"><Stat icon={<MessageCircle size={18}/>} label="Messages analyzed" value={appStatus.demo_mode?"128":String(analytics?.total_messages ?? 0)} note={appStatus.demo_mode?"Sample session":"Current session"}/><Stat icon={<Sparkles size={18}/>} label="Challenges launched" value={launched?"1":"0"} note="This session"/><Stat icon={<Users size={18}/>} label="Unique participants" value={appStatus.demo_mode?"24":String(analytics?.unique_chatters ?? 0)} note={appStatus.demo_mode?"Illustrative data":"Current session"}/><Stat icon={<Gauge size={18}/>} label="Open questions" value={appStatus.demo_mode?"—":String(analytics?.groups_open ?? 0)} note={appStatus.demo_mode?"Demo estimate":"Smart Queue"}/></section>
        <section className="studio-grid">
          <div className="panel chat-panel"><div className="panel-heading"><div><div className="panel-title"><MessageCircle size={17}/> Chat signals</div><p>Recent conversation context</p></div><span className="subtle-pill">{appStatus.twitch_authorized?"LIVE CHAT":appStatus.demo_mode?"SAMPLE DATA":"NOT CONNECTED"}</span></div><div className="chat-list" ref={chatListRef}>{chatMessages.length ? chatMessages.slice(-30).map((message,index)=><Chat key={`${message.received_at}-${index}-${message.display_name}`} initials={message.display_name.slice(0,1).toUpperCase()} name={message.display_name} color={["purple","teal","orange","blue","pink"][index%5]} text={message.text} source={message.is_synthetic?"SYNTHETIC REPLAY":appStatus.demo_mode?"sample":"Twitch"}/>) : <div className="chat-empty" role="status">{chatFeedError ? <>{chatFeedError} <a href="/auth/twitch">Reconnect Twitch</a> to restore live chat access.</> : appStatus.demo_mode ? "Synthetic demo chat is loading." : appStatus.twitch_authorized ? "No Twitch chat messages received yet. Send a harmless test message in your connected channel to verify delivery." : <>Connect Twitch to receive live chat. <a href="/auth/twitch">Connect Twitch</a></>}</div>}</div><div className="chat-foot"><span className="pulse-dot"/> {appStatus.twitch_authorized?(chatFeedError?"Live chat needs attention":chatMessages.length?"Live Twitch chat":"Waiting for first chat message"):appStatus.demo_mode?"Synthetic sample chat":"Twitch not connected"}</div></div>
           <SmartQueue onError={setApiError}/><div className="panel challenge-panel"><div className="panel-heading"><div><div className="panel-title"><Sparkles size={17} className="purple-text"/> Challenge suggestion</div><p>Ask Gemini on demand, then review before launch</p></div><span className="ai-tag"><Bot size={13}/> {launched?"APPROVED":"DRAFT"}</span></div><div className="signal-callout"><span className="signal-icon"><Activity size={16}/></span><span><strong>Community context</strong><small>Chat is sent for analysis only when you request a suggestion.</small></span></div><button className="button button-secondary" disabled={suggesting||busy} onClick={requestSuggestion}><Sparkles size={15}/>{suggesting?"Generating…":"Generate suggestion"}</button><label className="field-label" htmlFor="challenge-title">CHALLENGE TITLE</label><input id="challenge-title" className="text-input" value={title} onChange={(e)=>setTitle(e.target.value)}/><label className="field-label" htmlFor="challenge-question">QUESTION</label><textarea id="challenge-question" className="text-input question-input" value={question} onChange={(e)=>setQuestion(e.target.value)}/><div className="option-label-row"><label className="field-label">VOTING OPTIONS</label><span>{options.length} options</span></div>{options.map((option,index)=><div className="option-row" key={index}><span className="option-number">{index+1}</span><input aria-label={`Voting option ${index+1}`} className="option-editor" value={option} onChange={(event)=>setOptions(current=>current.map((item,position)=>position===index?event.target.value:item))}/><span className={`option-color ${index===0?"purple":"slate"}`}/></div>)}<div className="challenge-footer"><label className="duration"><Activity size={15}/> Duration <input aria-label="Challenge duration in seconds" className="duration-input" type="number" min={15} max={60} value={duration} onChange={(event)=>setDuration(Math.max(15,Math.min(60,Number(event.target.value)||15)))}/> seconds</label><button className="button button-primary" disabled={busy||launched} onClick={approveAndLaunch}><Check size={16}/>{busy?"Launching…":launched?"Challenge launched":"Approve & launch"}</button></div>{launched&&<div className="success-note"><Check size={14}/> Challenge launched through the backend. {appStatus.twitch_authorized?"Twitch chat can vote.":"Twitch chat remains disconnected."}</div>}</div>
          <AudienceVotePanel currentChallenge={currentChallenge} currentChallengeIsPoll={currentChallengeIsPoll} showVotePreview={showVotePreview} voteOptions={voteOptions} votes={votes} total={total} setVotes={setVotes}/>
        </section>
        <footer className="page-footer"><span><span className="footer-dot"/> StreamPilot AI <span>·</span> Local starter preview</span><span>Built for creators, powered by community.</span></footer>
        </> : activeNav === "Insights" ? <Insights sessionId={summarySessionId}/> : activeNav === "Moderation" ? <Moderation/> : <WorkspacePage activeNav={activeNav} status={appStatus} overlayUrl={overlayUrl} onGenerateOverlay={generateOverlayLink} />}
      </div>
    </main>
    {showEndSessionModal && <div className="modal-backdrop" role="presentation"><section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="end-session-title" aria-describedby="end-session-description"><div className="confirm-modal-icon"><Activity size={19}/></div><h2 id="end-session-title">End this stream session?</h2><p id="end-session-description">The current session will close and its recap will be prepared. You can reconnect Twitch when you are ready to start a new session.</p><div className="confirm-modal-actions"><button className="button button-secondary" onClick={() => setShowEndSessionModal(false)}>Cancel</button><button className="button button-primary" onClick={() => void confirmEndSession()}>End session</button></div></section></div>}
  </div>;
}
function Stat({icon,label,value,note}:{icon:React.ReactNode;label:string;value:string;note:string}){return <div className="stat-card"><div className="stat-top"><span className="stat-icon">{icon}</span><span className="stat-note">{note}</span></div><div className="stat-value">{value}</div><div className="stat-label">{label}</div></div>}
function Chat({initials,name,color,text,source}:{initials:string;name:string;color:string;text:string;source:string}){return <div className="chat-message"><div className={`chat-avatar ${color}`}>{initials}</div><div className="chat-body"><div className="chat-meta"><strong>{name}</strong><span>{source}</span></div><p>{text}</p></div></div>}
function Vote({label,count,total,color}:{label:string;count:number;total:number;color:string}){const pct=total>0?Math.round(count/total*100):0;return <div className="vote-option"><div className="vote-copy"><span>{label}</span><strong>{pct}%</strong></div><div className="progress-track"><div className={`progress-fill ${color}`} style={{width:`${pct}%`}}/></div></div>}
function AudienceVotePanel({currentChallenge,currentChallengeIsPoll,showVotePreview,voteOptions,votes,total,setVotes}:{currentChallenge:Challenge|null;currentChallengeIsPoll:boolean;showVotePreview:boolean;voteOptions:Array<{label:string;votes?:number}>;votes:number[];total:number;setVotes:(update:number[]|((current:number[])=>number[]))=>void}){return <section className="panel vote-panel"><div className="panel-heading"><div><div className="panel-title"><Trophy size={17}/> {currentChallengeIsPoll&&currentChallenge?.status==="completed"?"Audience poll results":"Audience vote"}</div><p>{currentChallenge?.status === "active" ? "Live totals from the backend" : currentChallenge?.status === "completed" ? "Final results from the existing vote records" : showVotePreview ? "Sample preview until a challenge is active" : "No active audience vote"}</p></div><span className={`vote-state ${currentChallenge?.status === "active"?"state-live":""}`}><span/>{currentChallenge?.status?.toUpperCase() ?? (showVotePreview ? "PREVIEW" : "IDLE")}</span></div>{(currentChallenge || showVotePreview) ? <>{voteOptions.map((option,index)=><Vote key={option.label} label={option.label} count={option.votes ?? votes[index] ?? 0} total={currentChallenge?.total_votes ?? total} color={index===0?"purple-fill":"slate-fill"}/>) }<div className="vote-summary"><span><Users size={15}/>{currentChallenge?.total_votes ?? total} {currentChallenge ? "backend votes" : "sample votes"}</span>{currentChallenge?.status==="completed"&&currentChallengeIsPoll&&<strong>{currentChallenge.winning_option_id===null?"Tie · no winning option":`Winner: ${currentChallenge.options.find(option=>option.id===currentChallenge.winning_option_id)?.label ?? "Final result"}`}</strong>}{showVotePreview && <button onClick={()=>setVotes(v=>[v[0]+1,v[1]])}>Simulate preview vote</button>}</div></> : <div className="chat-empty" role="status">Start a challenge or approve a poll suggestion to see live audience votes here.</div>}</section>}
function Safety({title,text}:{title:string;text:string}){return <div className="safety-item"><span className="safety-check"><Check size={13}/></span><div><strong>{title}</strong><p>{text}</p></div></div>}
function IntegrationStatus({status}:{status:AppStatus}){return <div className="integration-status"><strong>Integration status</strong><span>{status.integrations_verified?"Verified end to end":"Configured and authorized; full end-to-end verification pending"}</span><p>{status.integrations_verified?"Provider, EventSub, Gemini and chat actions have all been verified in a controlled run.":"Credentials and authorization can be present while the verified flag remains off until a complete controlled run records provider, EventSub, chat and approval evidence."}</p></div>}
function SettingsPanel({status}:{status:AppStatus}){
  const [streamFacts,setStreamFacts]=useState("");
  const [factsSaved,setFactsSaved]=useState(false);
  const [factsBusy,setFactsBusy]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{api.getStreamFacts().then((result)=>setStreamFacts(result.stream_facts)).catch((reason:unknown)=>setError(reason instanceof Error?reason.message:"Settings are unavailable."));},[]);
  return <div className="settings-layout"><div className="settings-safety"><IntegrationStatus status={status}/><div className="panel-title"><ShieldCheck size={17}/> Safety guardrails</div><p>Protection rules that apply across the workspace.</p><Safety title="Approval required" text="AI suggestions never launch automatically."/><Safety title="Vote validation" text="Backend validates chat commands and timing."/><Safety title="Emergency stop" text="End active interactions at any time."/></div><section className="panel settings-panel"><div className="panel-title"><Settings size={17}/> Integration settings</div><Safety title="Gemini" text={status.gemini_configured?"Backend credentials are configured.":"Set GEMINI_API_KEY and GEMINI_MODEL in backend/.env."}/><Safety title="Twitch" text={status.twitch_authorized?"Broadcaster authorization is saved.":"Set up your Twitch app and connect a broadcaster."}/><div className="stream-facts"><label htmlFor="stream-facts">Stream facts for grounded AI replies</label><p>Share only facts you want Gemini to use, such as PC specs, schedule, or socials. If an answer is not supported here, replies use a safe holding message. Do not enter secrets.</p><textarea id="stream-facts" maxLength={3000} value={streamFacts} onChange={(event)=>{setStreamFacts(event.target.value);setFactsSaved(false);}} placeholder="Example: I stream Tue/Thu at 7 PM. GPU: RTX 4070. Twitch is my only social."/><div><span>{streamFacts.length}/3000</span><button className="button button-secondary" disabled={factsBusy} onClick={()=>{setFactsBusy(true);setError("");api.saveStreamFacts(streamFacts).then((result)=>{setStreamFacts(result.stream_facts);setFactsSaved(true);}).catch((reason:unknown)=>setError(reason instanceof Error?reason.message:"Could not save stream facts.")).finally(()=>setFactsBusy(false));}}>{factsBusy?"Saving…":"Save stream facts"}</button></div>{factsSaved&&<p className="facts-saved" role="status">Stream facts saved.</p>}{error&&<p role="alert" className="reply-error">{error}</p>}</div><p>Native Twitch polls are not available for this channel. Approved polls use StreamPilot's !vote N flow.</p><p>Secrets stay on the backend. This page never displays secret values.</p></section></div>}

function WorkspacePage({activeNav,status,overlayUrl,onGenerateOverlay}:{activeNav:string;status:AppStatus;overlayUrl:string;onGenerateOverlay:()=>void}) {
  const safetyGuardrails = <><IntegrationStatus status={status}/><div className="settings-safety"><div className="panel-title"><ShieldCheck size={17}/> Safety guardrails</div><p>Protection rules that apply across the workspace.</p><Safety title="Approval required" text="AI suggestions never launch automatically."/><Safety title="Vote validation" text="Backend validates chat commands and timing."/><Safety title="Emergency stop" text="End active interactions at any time."/></div></>;
  const [history,setHistory] = useState<Challenge[]>([]);
  const [error,setError] = useState("");
  const [streamFacts,setStreamFacts] = useState("");
  const [factsSaved,setFactsSaved] = useState(false);
  const [factsBusy,setFactsBusy] = useState(false);
  useEffect(() => {
    if (activeNav === "Activity") api.listChallenges().then(setHistory).catch((reason:unknown)=>setError(reason instanceof Error?reason.message:"History is unavailable."));
  }, [activeNav]);
  useEffect(() => {
    if (activeNav === "Settings") {
      api.getStreamFacts().then((result)=>setStreamFacts(result.stream_facts)).catch((reason:unknown)=>setError(reason instanceof Error?reason.message:"Settings are unavailable."));
    }
  }, [activeNav]);
  if (activeNav === "Overlay Setup") return <section className="panel"><div className="panel-title"><Radio size={17}/> Protected browser overlay</div><p>Generate a private OBS browser source link for this session.</p><button className="button button-primary" onClick={onGenerateOverlay}>Generate overlay link</button>{overlayUrl&&<p><a href={overlayUrl} target="_blank" rel="noreferrer">Open overlay preview</a><br/><code>{overlayUrl}</code></p>}</section>;
  if (activeNav === "Activity") return <section className="panel"><div className="panel-title"><Activity size={17}/> Challenge history</div>{error&&<p role="alert">{error}</p>}{history.length===0&&!error&&<p>No saved challenges yet.</p>}{history.map((item)=><div className="option-row" key={item.id}><strong>{item.title}</strong><span>{item.status}</span><span>{item.total_votes} votes</span></div>)}</section>;
  if (activeNav === "Settings") return <SettingsPanel status={status}/>;
  return <section className="panel"><div className="panel-title"><LayoutDashboard size={17}/> Workspace overview</div><p>{status.demo_mode?"Demo mode is active; sample metrics are synthetic.":"Live mode is active; real integrations are not considered verified until tested."}</p><div className="option-row"><span>Gemini</span><strong>{status.gemini_configured?"Configured":"Not configured"}</strong></div><div className="option-row"><span>Twitch</span><strong>{status.twitch_authorized?"Authorized":"Not connected"}</strong></div><div className="option-row"><span>Integration verification</span><strong>{status.integrations_verified?"Verified":"Not verified"}</strong></div></section>;
}
