import { useEffect, useState, type ReactNode } from "react";
import { Clipboard, LoaderCircle, MessageCircle, Users, CircleHelp, CheckCircle2 } from "lucide-react";
import { api, type AnalyticsSnapshot, type SessionSummary } from "./api";
import "./insights.css";

export default function Insights({ sessionId }: { sessionId: number | null }) {
  const [stats, setStats] = useState<AnalyticsSnapshot | null>(null);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () => api.getCurrentAnalytics().then(value => { if (active) setStats(value); }).catch(() => undefined);
    void refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!sessionId) {
      let active = true;
      const refreshLatest = () => api.getLatestSessionSummary().then(value => { if(active) setSummary(value); }).catch(() => undefined);
      void refreshLatest();
      const timer = window.setInterval(refreshLatest, 3000);
      return () => { active = false; window.clearInterval(timer); };
    }
    let active = true;
    const refresh = () => api.getSessionSummary(sessionId).then(value => { if(active) setSummary(value); }).catch(() => undefined);
    void refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [sessionId]);
  async function copyMarkdown() {
    if (!summary?.content) return;
    const c = summary.content;
    const text = [`# ${c.headline}`, "", "## Top topics", ...c.top_topics.map(x=>`- ${x}`), "", "## Frequently asked", ...c.frequently_asked_questions.map(x=>`- ${x.question} (${x.status})`), "", "## Follow up", ...c.unanswered_follow_ups.map(x=>`- ${x}`), "", `**Audience observation:** ${c.audience_mood_observation}`, "", "## Content ideas", ...c.content_suggestions.map(x=>`- ${x}`)].join("\n");
    await navigator.clipboard.writeText(text);
    setCopied(true); window.setTimeout(()=>setCopied(false),1800);
  }
  return <div className="insights-page">
    <section className="insights-stats">
      <Metric icon={<MessageCircle/>} label="Chat messages" value={stats?.total_messages ?? 0}/>
      <Metric icon={<Users/>} label="Unique chatters" value={stats?.unique_chatters ?? 0}/>
      <Metric icon={<CircleHelp/>} label="Questions detected" value={stats?.questions_detected ?? 0}/>
      <Metric icon={<CheckCircle2/>} label="Answered groups" value={stats?.groups_answered ?? 0}/>
    </section>
    <section className="panel insight-panel">
      <div className="panel-heading"><div><div className="panel-title">Messages per minute</div><p>Last 30 minutes · persisted chat</p></div><span className="subtle-pill">LIVE SNAPSHOT</span></div>
      <div className="minute-chart" role="img" aria-label="Messages per minute over the last 30 minutes">{(stats?.messages_per_minute ?? []).map((point,index)=>{
        const max = Math.max(1,...(stats?.messages_per_minute ?? []).map(p=>p.count));
        return <div className="minute-column" key={`${point.minute}-${index}`} title={`${new Date(point.minute).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}: ${point.count} messages`}><i style={{height:`${Math.max(3,point.count/max*100)}%`}}/><span>{index%5===0?new Date(point.minute).toLocaleTimeString([], {hour:"2-digit"}):""}</span></div>;
      })}</div>
    </section>
    <section className="panel insight-panel"><div className="panel-heading"><div><div className="panel-title">Top unanswered questions</div><p>Open questions ranked by viewer count</p></div></div>
      {stats?.top_unanswered_questions.length ? <ul className="insight-list">{stats.top_unanswered_questions.map(q=><li key={q.id}><span>{q.canonical_text}</span><b>{q.asker_count} viewers</b></li>)}</ul> : <p className="insight-empty">No unanswered questions in the active session.</p>}
    </section>
    <section className="panel insight-panel recap-panel"><div className="panel-heading"><div><div className="panel-title">End-of-stream recap</div><p>{summary?.status === "complete" ? "Generated from aggregate queue and poll data" : "A recap is created after you end a session"}</p></div>{summary?.content&&<button className="button button-secondary" onClick={copyMarkdown}><Clipboard size={14}/>{copied?"Copied":"Copy as markdown"}</button>}</div>
      {summary?.content ? <><h2>{summary.content.headline}</h2><p className="mood-note">{summary.content.audience_mood_observation}</p><div className="recap-columns"><div><strong>Topics</strong>{summary.content.top_topics.map(x=><span key={x}>{x}</span>)}</div><div><strong>Follow up</strong>{summary.content.unanswered_follow_ups.map(x=><span key={x}>{x}</span>)}</div><div><strong>Content ideas</strong>{summary.content.content_suggestions.map(x=><span key={x}>{x}</span>)}</div></div></> : <div className="insight-empty">{summary?.status === "pending" ? <><LoaderCircle className="spin" size={16}/> Generating recap in the background…</> : "End a stream session to create a recap."}</div>}
    </section>
  </div>;
}
function Metric({icon,label,value}:{icon:ReactNode;label:string;value:number}) { return <div className="panel insight-metric"><span>{icon}</span><small>{label}</small><strong>{value.toLocaleString()}</strong></div>; }
