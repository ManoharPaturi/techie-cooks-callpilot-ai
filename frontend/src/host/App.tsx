import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { AudioEngine } from '../audio/capture';
import { HostCall, type CallState } from '../call/webrtc';
import type {
  AsrState, CallSummary, CoachSuggestion, EvidenceLine, SafetyAssessment, SafetyCandidate,
  ServerEvent, Source, TaskProposal, Utterance,
} from '../types/events';

interface Status {
  asr: { model: string; ok: boolean; location: string };
  llm: { model: string; ok: boolean; location: string };
  assistant?: { model: string; location: string };
  summary?: { model: string; location: string };
  notes: { filename: string }[];
  paragraph_count: number;
  llm_pending: number;
  asr_pending: number;
}

const CATEGORY_LABEL: Record<string, string> = {
  otp_or_code: 'OTP / security code', password: 'Password', payment: 'Money transfer',
  remote_access: 'Remote-access app', pressure: 'Pressure / threat', other: 'Sensitive request',
  upi_pin: 'UPI PIN', kyc_update: 'KYC / Aadhaar / PAN', digital_arrest: 'Fake police / CBI',
  parcel_scam: 'Parcel / customs',
};

const DEMO_TRACKS = [
  { id: 'remote_scam', label: 'Fake bank “fraud team” asks for your OTP' },
  { id: 'remote_legit', label: 'Client Priya asks about the website deal' },
  { id: 'remote_digital_arrest', label: 'Fake CBI “digital arrest” + UPI PIN' },
  { id: 'remote_injection', label: 'Caller tries to hijack the assistant' },
];

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } });
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
  return res.json() as Promise<T>;
}

const fmtTime = (ms: number) => {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [consent, setConsent] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [utterances, setUtterances] = useState<Utterance[]>([]);
  const [asrState, setAsrState] = useState<Record<Source, AsrState>>({ HOST: 'idle', REMOTE: 'idle' });
  const [candidates, setCandidates] = useState<Record<string, SafetyCandidate>>({});
  const [assessments, setAssessments] = useState<Record<string, SafetyAssessment>>({});
  const [suggestions, setSuggestions] = useState<CoachSuggestion[]>([]);
  const [summary, setSummary] = useState<CallSummary | null>(null);
  const [tasks, setTasks] = useState<Record<string, TaskProposal>>({});
  const [asking, setAsking] = useState(false);
  const [autoSuggest, setAutoSuggest] = useState(true);
  const [question, setQuestion] = useState('');

  const engine = useRef<AudioEngine | null>(null);
  const sessionRef = useRef<string | null>(null);
  const audioEl = useRef<HTMLAudioElement | null>(null);
  const [track, setTrack] = useState(DEMO_TRACKS[0].id);
  const [customUrl, setCustomUrl] = useState<{ url: string; name: string } | null>(null);
  const [levels, setLevels] = useState({ HOST: 0, REMOTE: 0, sentHOST: 0, sentREMOTE: 0, dropped: 0 });
  const [sampleRate, setSampleRate] = useState(0);
  const [micOk, setMicOk] = useState<boolean | null>(null);

  // ---------- live mode ----------
  const [mode, setMode] = useState<'replay' | 'live'>('replay');
  const [liveCfg, setLiveCfg] = useState<{ guest_enabled: boolean; guest_base_url: string | null } | null>(null);
  const [joinUrl, setJoinUrl] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [callState, setCallState] = useState<CallState | null>(null);
  const [needsAudioTap, setNeedsAudioTap] = useState(false);
  const hostCall = useRef<HostCall | null>(null);
  const remoteAudio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => { api<typeof liveCfg>('/api/live-config').then(setLiveCfg).catch(() => {}); }, []);

  // ---------- status polling ----------
  useEffect(() => {
    const load = () => api<Status>('/api/status').then(setStatus).catch(() => setStatus(null));
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  // ---------- event stream ----------
  useEffect(() => {
    let ws: WebSocket;
    let stopped = false;
    const connect = () => {
      ws = new WebSocket(`ws://${location.host}/ws/events`);
      ws.onmessage = (e) => handleEvent(JSON.parse(e.data) as ServerEvent);
      ws.onclose = () => { if (!stopped) setTimeout(connect, 1000); };
    };
    connect();
    return () => { stopped = true; ws?.close(); };
  }, []);

  const handleEvent = useCallback((ev: ServerEvent) => {
    // Ignore events from any other (stale) session.
    const sid = (ev as { session_id?: string }).session_id;
    if (sid && sid !== sessionRef.current) return;
    switch (ev.type) {
      case 'transcript.final':
        setUtterances((u) => [...u, ev]);
        break;
      case 'asr.status':
        setAsrState((s) => ({ ...s, [ev.source]: ev.state }));
        break;
      case 'safety.candidate':
        setCandidates((c) => ({ ...c, [ev.trigger_id]: ev }));
        break;
      case 'safety.assessment':
        setAssessments((a) => ({ ...a, [ev.trigger_id]: ev }));
        break;
      case 'coach.suggestion':
        setSuggestions((s) => [ev, ...s]);
        if (!ev.proactive) setAsking(false);
        break;
      case 'call.summary':
        setSummary(ev);
        setTasks(Object.fromEntries(ev.tasks.map((t) => [t.id, t])));
        break;
      case 'room.status':
        if (ev.state === 'closed') setCallState((c) => (c && c !== 'ended' ? 'ended' : c));
        break;
      case 'task.updated':
        setTasks((t) => ({ ...t, [ev.task.id]: ev.task }));
        break;
    }
  }, []);

  // ---------- meters ----------
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => {
      const t = engine.current?.taps;
      setLevels({
        HOST: t?.HOST?.peak ?? 0, REMOTE: t?.REMOTE?.peak ?? 0,
        sentHOST: t?.HOST?.framesSent ?? 0, sentREMOTE: t?.REMOTE?.framesSent ?? 0,
        dropped: (t?.HOST?.framesDropped ?? 0) + (t?.REMOTE?.framesDropped ?? 0),
      });
      if (t?.HOST) t.HOST.peak *= 0.6;
      if (t?.REMOTE) t.REMOTE.peak *= 0.6;
    }, 100);
    return () => clearInterval(id);
  }, [running]);

  // ---------- session control ----------
  const start = async () => {
    setError(null);
    setUtterances([]); setCandidates({}); setAssessments({}); setSuggestions([]); setSummary(null); setTasks({});
    try {
      const { session_id } = await api<{ session_id: string }>('/api/sessions', {
        method: 'POST', body: JSON.stringify({ consent }),
      });
      sessionRef.current = session_id;
      const eng = new AudioEngine();
      await eng.init(); // inside the click gesture
      engine.current = eng;
      setSampleRate(eng.sampleRate);
      setSessionId(session_id);
      setRunning(true);
      try {
        await eng.startMic(session_id, mode);
        setMicOk(true);
      } catch (e) {
        setMicOk(false);
        setError(`Microphone unavailable: ${(e as Error).message}.${mode === 'replay' ? ' Replay still works.' : ' Live calls need the mic.'}`);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const endLiveCall = useCallback(() => {
    hostCall.current?.close();
    hostCall.current = null;
    engine.current?.detachLiveRemote();
    if (remoteAudio.current) remoteAudio.current.srcObject = null;
    setJoinUrl(null); setQr(null); setNeedsAudioTap(false);
  }, []);

  const createRoom = async () => {
    setError(null);
    const eng = engine.current;
    if (!eng?.micStream) { setError('Live calls need your microphone. Allow mic access and restart the session.'); return; }
    try {
      endLiveCall();
      const r = await api<{ join_url: string }>('/api/rooms', { method: 'POST' });
      setJoinUrl(r.join_url);
      setQr(await QRCode.toDataURL(r.join_url, { margin: 1, width: 240 }));
      const call = new HostCall(eng.micStream, async (stream) => {
        const el = remoteAudio.current!;
        el.srcObject = stream; // the native <audio> element owns live playback
        try { await el.play(); setNeedsAudioTap(false); } catch { setNeedsAudioTap(true); }
        if (sessionRef.current) await engine.current?.attachLiveRemote(sessionRef.current, stream);
      }, (state, detail) => {
        setCallState(state);
        if (detail) setError(detail);
        if (state === 'ended') { endLiveCall(); }
      });
      call.connect();
      hostCall.current = call;
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const enableCallAudio = async () => {
    try { await remoteAudio.current?.play(); setNeedsAudioTap(false); } catch (e) { setError((e as Error).message); }
  };

  const stop = async () => {
    endLiveCall();
    setCallState(null);
    audioEl.current?.pause();
    await engine.current?.stop();
    engine.current = null;
    setRunning(false);
    setMicOk(null);
    setAsrState({ HOST: 'idle', REMOTE: 'idle' });
    setLevels({ HOST: 0, REMOTE: 0, sentHOST: 0, sentREMOTE: 0, dropped: 0 });
    if (sessionId) await api(`/api/sessions/${sessionId}/stop`, { method: 'POST' }).catch(() => {});
  };

  const playRemote = async () => {
    if (!engine.current || !sessionId || !audioEl.current) return;
    setError(null);
    try {
      await engine.current.attachReplay(sessionId, audioEl.current);
      await audioEl.current.play();
    } catch (e) {
      setError(`Replay could not start: ${(e as Error).message}`);
    }
  };

  const replaySrc = customUrl?.url ?? `/api/demo-audio/${track}`;

  // ---------- notes / ask ----------
  const loadDemoNote = async () => {
    await api('/api/notes/demo/client_agreement', { method: 'POST' }).catch((e) => setError(e.message));
    setStatus(await api<Status>('/api/status'));
  };
  const importNote = async (file: File) => {
    const text = await file.text();
    await api('/api/notes', { method: 'POST', body: JSON.stringify({ filename: file.name, text }) })
      .catch((e) => setError(e.message));
    setStatus(await api<Status>('/api/status'));
  };
  const clearNotes = async () => {
    await api('/api/notes', { method: 'DELETE' });
    setStatus(await api<Status>('/api/status'));
  };
  const toggleAuto = async (enabled: boolean) => {
    setAutoSuggest(enabled);
    if (sessionId) await api(`/api/sessions/${sessionId}/auto-suggest`, { method: 'POST', body: JSON.stringify({ enabled }) }).catch(() => {});
  };
  const ask = useCallback(async (q: string) => {
    if (!sessionId) return;
    setAsking(true);
    await api('/api/ask', { method: 'POST', body: JSON.stringify({ session_id: sessionId, question: q }) })
      .catch((e) => { setError(e.message); setAsking(false); });
  }, [sessionId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && e.code === 'KeyA') { e.preventDefault(); ask(question); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ask, question]);

  const byId = useMemo(() => Object.fromEntries(utterances.map((u) => [u.id, u])), [utterances]);
  const alerts = useMemo(() => {
    const ids = new Set([...Object.keys(candidates), ...Object.keys(assessments)]);
    return [...ids].sort().reverse().map((id) => ({ id, candidate: candidates[id], assessment: assessments[id] }));
  }, [candidates, assessments]);

  const newSession = () => {
    setSessionId(null); sessionRef.current = null;
    setUtterances([]); setCandidates({}); setAssessments({}); setSuggestions([]); setSummary(null); setTasks({});
    setError(null); setCallState(null); setConsent(false); setBannerDismissed(false);
  };

  const conversation = useMemo(() => [...utterances].sort((a, b) => a.start_ms - b.start_ms), [utterances]);
  const flagFor = (id: string): Flag | null => {
    const a = assessments[id];
    if (a) return a.alert;
    return candidates[id] ? 'checking' : null;
  };
  const dangerCount = Object.values(assessments).filter((a) => a.alert === 'danger').length;
  const verdict = useMemo(() => scamVerdict(Object.values(assessments)), [assessments]);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  // ---------------- start screen ----------------
  if (!sessionId) {
    return (
      <div className="shell">
        <TopBar status={status} />
        {error && <div className="banner" role="alert">{error}</div>}
        <main className="start">
          <section className="hero">
            <p className="eyebrow">Private call copilot · runs entirely on this Mac</p>
            <h1>Hears both sides.<br /><em>Advises only you.</em></h1>
            <p className="lede">
              CallPilot writes down what you and the caller say in two lanes, flags requests for codes, passwords or
              money the moment they happen, and answers your questions from your own notes. The caller never sees any of it.
            </p>
            <figure className="preview" aria-label="Example of what CallPilot shows">
              <figcaption>Example</figcaption>
              <div className="pv-line caller">
                <div />
                <div className="pv-spine"><span className="pin danger" /><time>00:16</time></div>
                <div className="pv-cell">
                  <p className="pv-bubble">Please tell me the OTP right now, so I can unlock your account.</p>
                  <p className="pv-alert">Possible request for your OTP. Don’t share it — call your bank on its official number.</p>
                </div>
              </div>
              <div className="pv-line you">
                <div className="pv-cell"><p className="pv-bubble">Let me call the bank back first.</p></div>
                <div className="pv-spine"><span className="pin" /><time>00:21</time></div>
                <div />
              </div>
            </figure>
          </section>

          <section className="start-card" aria-labelledby="start-title">
            <h2 id="start-title">Start a call session</h2>
            <div className="seg" role="radiogroup" aria-label="Caller audio">
              <label className={mode === 'replay' ? 'on' : ''}>
                <input type="radio" name="mode" checked={mode === 'replay'} onChange={() => setMode('replay')} />
                <b>Replay a recording</b><span>Play a sample caller through your speakers</span>
              </label>
              <label className={`${mode === 'live' ? 'on' : ''} ${liveCfg?.guest_enabled ? '' : 'off'}`}>
                <input type="radio" name="mode" checked={mode === 'live'} disabled={!liveCfg?.guest_enabled} onChange={() => setMode('live')} />
                <b>Live phone call</b>
                <span>{liveCfg?.guest_enabled ? 'A phone joins by scanning a QR code' : 'Connect to your iPhone hotspot and relaunch'}</span>
              </label>
            </div>
            <ul className="promises">
              <li><Dot ok={status?.asr.ok} /><span>Speech to text: <b>Whisper {short(status?.asr.model)}</b>, on this Mac</span></li>
              <li><Dot ok={status?.llm.ok} /><span>Scam checks{status?.assistant?.model === status?.llm.model ? ' and live answers' : ''}: <b>{prettyModel(status?.llm.model)}</b>, on this Mac</span></li>
              {status?.assistant && status.assistant.model !== status.llm.model &&
                <li><Dot ok={status?.llm.ok} /><span>Live answers: <b>{prettyModel(status.assistant.model)}</b>, on this Mac</span></li>}
              {status?.summary && status.summary.model !== status.llm.model &&
                <li><Dot ok={status?.llm.ok} /><span>After-call summary: <b>{prettyModel(status.summary.model)}</b>, on this Mac</span></li>}
              <li><Dot ok /><span>No cloud. Raw audio is never saved.</span></li>
            </ul>
            <label className="consent">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>I agree to local microphone capture and transcription, and the other person knows this call is transcribed.</span>
            </label>
            <button className="btn primary xl" disabled={!consent} onClick={start}>Start session</button>
            <p className="hint">Wear headphones so your microphone hears only you.</p>
          </section>
        </main>
        <footer className="foot">
          CallPilot AI · built by Techie Cooks for Hacktoberfest Hack Day Coimbatore 2026 · open source (MIT) ·
          speech, scam checks and summaries run on this Mac with whisper.cpp, Qwen3 and Gemma 4.
        </footer>
      </div>
    );
  }

  // ---------------- workspace ----------------
  return (
    <div className="shell">
      <TopBar status={status} micOk={micOk} mode={mode} callState={callState} running={running}
        onEnd={stop} onNew={newSession} />
      {error && <div className="banner" role="alert">{error}<button className="x" aria-label="Dismiss" onClick={() => setError(null)}>×</button></div>}

      <main className="work">
        <section className="stage">
          <div className="source">
            {mode === 'replay' ? (
              <div className="src-row">
                <div className="src-pick">
                  <span className="label">Caller audio</span>
                  <div className="row">
                    <select value={customUrl ? 'custom' : track} disabled={!!customUrl} onChange={(e) => setTrack(e.target.value)} aria-label="Recording">
                      {DEMO_TRACKS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                      {customUrl && <option value="custom">{customUrl.name}</option>}
                    </select>
                    <label className="btn ghost sm file">Use my WAV
                      <input type="file" accept="audio/wav,.wav" onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) setCustomUrl({ url: URL.createObjectURL(f), name: f.name });
                      }} />
                    </label>
                    {customUrl && <button className="btn link" onClick={() => setCustomUrl(null)}>Back to samples</button>}
                  </div>
                </div>
                <div className="src-play">
                  <audio key={sessionId} id="remoteReplay" ref={audioEl} controls preload="auto" src={replaySrc} />
                  <button className="btn primary" disabled={!running} onClick={playRemote}>▶ Play caller audio</button>
                </div>
              </div>
            ) : (
              <div className="src-row live">
                <div className="src-pick">
                  <span className="label">Caller audio · phone</span>
                  {!joinUrl
                    ? <button className="btn primary" disabled={!running} onClick={createRoom}>Create call link</button>
                    : <>
                        <code className="join">{joinUrl}</code>
                        <div className="row">
                          <button className="btn ghost sm" onClick={() => navigator.clipboard.writeText(joinUrl)}>Copy link</button>
                          <button className="btn ghost sm" onClick={createRoom}>New link</button>
                          <button className="btn link danger" onClick={() => { hostCall.current?.close(); endLiveCall(); setCallState('ended'); }}>Hang up</button>
                        </div>
                        <p className="hint">One phone, one use, valid 15 minutes.</p>
                      </>}
                  <audio ref={remoteAudio} autoPlay playsInline controls className="remote-audio" />
                  {needsAudioTap && <button className="btn primary" onClick={enableCallAudio}>🔊 Turn on call audio</button>}
                </div>
                {qr && <figure className="qr"><img src={qr} alt="QR code: scan with the phone to join" /><figcaption>Scan with the phone</figcaption></figure>}
              </div>
            )}
          </div>

          {verdict && !bannerDismissed && <HangUpBanner v={verdict} reportUrl={`/api/sessions/${sessionId}/report`} onDismiss={() => setBannerDismissed(true)} />}

          <Conversation items={conversation} flagFor={flagFor} asr={asrState} running={running} mode={mode}
            replied={new Set(suggestions.filter((x) => x.proactive && x.trigger_id).map((x) => x.trigger_id as string))}
            levels={levels} audioInfo={sampleRate ? `Captured at ${(sampleRate / 1000).toFixed(1)} kHz, resampled to 16 kHz on this Mac${levels.dropped ? ` · ${levels.dropped} frames dropped` : ''}` : ''} />
        </section>

        <aside className="rail">
          <section className="panel">
            <header className="panel-h">
              <h3><ShieldIcon />Safety</h3>
              <span className={`count ${dangerCount ? 'hot' : ''}`}>{dangerCount ? `${dangerCount} warning${dangerCount > 1 ? 's' : ''}` : 'No warnings'}</span>
            </header>
            {alerts.length === 0
              ? <p className="empty">Requests for codes, passwords, payments or remote access will show up here.</p>
              : <div className="alert-list">{alerts.map((a) => <AlertCard key={a.id} {...a} byId={byId} />)}</div>}
          </section>

          <section className="panel assistant">
            <header className="panel-h">
              <h3><SparkIcon />Assistant</h3>
              <div className="row">
                <label className="switch" title="Suggest answers automatically when the caller asks a question">
                  <input type="checkbox" checked={autoSuggest} onChange={(e) => toggleAuto(e.target.checked)} />
                  <span className="track" aria-hidden="true"><span className="knob" /></span>
                  Auto-suggest
                </label>
                <span className="only-you"><LockIcon />Only you</span>
              </div>
            </header>
            <form className="ask" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
              <input placeholder="Ask anything about this call…" value={question}
                onChange={(e) => setQuestion(e.target.value)} maxLength={400} aria-label="Ask privately" />
              <button className="btn primary" disabled={!sessionId || asking}>{asking ? 'Thinking…' : 'Ask'}</button>
            </form>
            <div className="notes">
              <span className="label">Notes</span>
              {status?.notes.length
                ? status.notes.map((n) => <span key={n.filename} className="chip file"><DocIcon />{n.filename}</span>)
                : <span className="muted">none yet</span>}
              <span className="spacer" />
              <button className="btn link" onClick={loadDemoNote}>Add sample</button>
              <label className="btn link file">Import
                <input type="file" accept=".md,.txt,text/markdown,text/plain" onChange={(e) => e.target.files?.[0] && importNote(e.target.files[0])} />
              </label>
              {!!status?.notes.length && <button className="btn link" onClick={clearNotes}>Clear</button>}
            </div>
            {suggestions.length === 0 && <p className="empty">{autoSuggest ? 'When the caller asks a question, a suggested answer appears here automatically. ' : ''}Ask about the call, your notes, or what to say next.</p>}
            {suggestions.map((s) => <Suggestion key={s.id} s={s} />)}
          </section>

          {(summary || (!running && sessionId)) && (
            <section className="panel">
              <header className="panel-h"><h3><CheckIcon />After the call</h3>
                <a className="btn sm" href={`/api/sessions/${sessionId}/report`} download>Download call report</a></header>
              {!summary && <p className="empty pulse">
                {status?.summary && status.summary.model !== status.llm.model
                  ? `${prettyModel(status.summary.model)} is writing your summary and follow-ups on this Mac — about a minute on an 8 GB laptop…`
                  : 'Writing a summary and follow-up tasks on this Mac…'}</p>}
              {summary && <>
                <p className="summary">{summary.summary}</p>
                {summary.caller_requests?.length > 0 && (
                  <div className="asked">
                    <h4>The caller asked you for</h4>
                    <ul>
                      {summary.caller_requests.map((r) => (
                        <li key={r.transcript_ids.join()}>
                          <b>{r.request}</b>
                          {r.evidence[0] && <q title={`Caller at ${fmtTime(r.evidence[0].start_ms)}`}>{r.evidence[0].text}</q>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {!summary.fallback_reason && <p className="written-by">Written by {prettyModel(summary.model)} on this Mac after the call</p>}
                {Object.values(tasks).length === 0 && <p className="empty">No follow-ups proposed.</p>}
                {Object.values(tasks).map((t) => <TaskEditor key={t.id} task={t} />)}
              </>}
            </section>
          )}
        </aside>
      </main>
      <footer className="foot">
        Lanes show where audio came from (your mic or the caller), not who is speaking. Transcripts and AI judgments can
        be wrong. Warnings describe behaviour; they are not proof of fraud.
      </footer>
    </div>
  );
}

type Flag = SafetyAssessment['alert'] | 'checking';

interface Verdict { pattern: string; action: string; report: boolean; count: number; at: string }

const HIGH_RISK = new Set(['digital_arrest', 'upi_pin', 'remote_access']);
// Most specific pattern first. Wording describes a pattern; it never declares the caller a criminal.
const PATTERNS: [string, string, string, boolean][] = [
  ['digital_arrest', 'a fake “digital arrest” scam', 'Hang up. No police or CBI officer arrests anyone over a video call.', true],
  ['upi_pin', 'a UPI “refund” scam', 'Hang up. Never enter your UPI PIN to receive money.', true],
  ['remote_access', 'a remote-access scam', 'Hang up. Don’t install any app or share your screen.', true],
  ['kyc_update', 'a fake KYC / SIM-block scam', 'Hang up. KYC is never done over a phone call.', true],
  ['parcel_scam', 'a fake parcel / customs scam', 'Hang up. Customs doesn’t phone to demand fees.', true],
  ['otp_or_code', 'a bank OTP scam', 'Hang up and call your bank on the number printed on your card.', false],
  ['payment', 'a money-transfer scam', 'Hang up. Don’t send money on a caller’s instructions.', false],
  ['password', 'a password-theft scam', 'Hang up. Don’t share passwords or login details.', false],
];

function scamVerdict(all: SafetyAssessment[]): Verdict | null {
  const danger = all.filter((a) => a.alert === 'danger');
  if (danger.length === 0) return null;
  if (danger.length < 2 && !danger.some((a) => HIGH_RISK.has(a.category))) return null;
  const cats = new Set(danger.map((a) => a.category));
  const [, pattern, action, report] = PATTERNS.find(([c]) => cats.has(c)) ?? ['', 'a common phone-scam', 'Hang up and verify through an official number.', false];
  const first = danger.map((a) => a.evidence[0]?.start_ms ?? 0).sort((x, y) => x - y)[0];
  return { pattern, action, report: report || danger.length >= 3, count: danger.length, at: fmtTime(first) };
}

function HangUpBanner({ v, reportUrl, onDismiss }: { v: Verdict; reportUrl: string; onDismiss: () => void }) {
  return (
    <section className="hangup" role="alert" aria-live="assertive">
      <span className="hangup-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.7 13.3a16 16 0 0 0 3.4 2.6l1.3-1.3a2 2 0 0 1 2.1-.4c.8.3 1.6.5 2.5.6a2 2 0 0 1 1.7 2v3a2 2 0 0 1-2.2 2A19.8 19.8 0 0 1 2.2 4.2 2 2 0 0 1 4.2 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.7.6 2.5a2 2 0 0 1-.4 2.1L8.1 9.6" /><path d="m22 2-20 20" /></svg></span>
      <div className="hangup-body">
        <p className="hangup-title">This call matches {v.pattern}.</p>
        <p className="hangup-action">{v.action}</p>
        <p className="hangup-meta">
          {v.count} suspicious request{v.count > 1 ? 's' : ''} since {v.at}
          {v.report && <> · Report it: call <b>1930</b> or visit <b>cybercrime.gov.in</b></>}
          {' · '}<a className="hangup-link" href={reportUrl} download>Save call report</a>
        </p>
      </div>
      <button className="x" aria-label="Dismiss" onClick={onDismiss}>×</button>
    </section>
  );
}

const ASK_TITLE: Record<string, string> = {
  otp_or_code: 'Asking for your OTP', password: 'Asking for your password', payment: 'Asking you to send money',
  remote_access: 'Asking for remote access', pressure: 'Pressure or threat', other: 'Sensitive request',
  upi_pin: 'Asking for your UPI PIN', kyc_update: 'Fake KYC / SIM-block demand',
  digital_arrest: '“Digital arrest” — fake police or CBI', parcel_scam: 'Fake parcel / customs threat',
};
const cap = (t: string) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);

const FLAG_LABEL: Record<Flag, string> = {
  danger: 'Suspicious request', caution: 'Caution', cleared: 'Safe — advice only', info: 'Mention only',
  uncertain: 'Unclear', checking: 'Checking…',
};

const short = (m?: string) => (m ?? '…').replace('ggml-', '');
const prettyModel = (m?: string) => {
  if (!m) return '…';
  const g = m.match(/^gemma(\d+):(.+)$/);
  if (g) return `Gemma ${g[1]} ${g[2].toUpperCase()}`;
  return m.replace('qwen3:', 'Qwen3 ').replace('b', 'B');
};

function Svg({ children, cls = 'ico' }: { children: React.ReactNode; cls?: string }) {
  return <svg className={cls} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>;
}
const LockIcon = () => <Svg><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" /><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" /></Svg>;
const ShieldIcon = () => <Svg><path d="M8 1.8 13 3.6v4.1c0 3-2.1 5.4-5 6.5-2.9-1.1-5-3.5-5-6.5V3.6z" /></Svg>;
const SparkIcon = () => <Svg><path d="M8 2v3M8 11v3M2 8h3M11 8h3M4 4l1.6 1.6M10.4 10.4 12 12M12 4l-1.6 1.6M5.6 10.4 4 12" /></Svg>;
const CheckIcon = () => <Svg><circle cx="8" cy="8" r="6" /><path d="m5.5 8.2 1.7 1.7 3.3-3.6" /></Svg>;

function AlertGlyph({ kind }: { kind: Flag }) {
  const path = kind === 'cleared' || kind === 'info'
    ? <path d="m5.5 8.2 1.7 1.7 3.3-3.6" />
    : kind === 'checking' ? <path d="M8 5v3l2 1.5" /> : <><path d="M8 4.8v3.6" /><path d="M8 11h.01" /></>;
  return <span className={`a-glyph ${kind}`}><Svg><circle cx="8" cy="8" r="6.2" />{path}</Svg></span>;
}

function DocIcon() {
  return (
    <svg className="doc" viewBox="0 0 12 14" aria-hidden="true">
      <path d="M1.5 1h6l3 3v9h-9z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M7.5 1v3h3M3.5 7.5h5M3.5 10h5" fill="none" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
}

function Dot({ ok }: { ok?: boolean }) {
  return <span className={`dot ${ok ? 'ok' : 'off'}`} aria-hidden="true" />;
}

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M13 7a9 9 0 0 0 0 18" fill="none" stroke="var(--you)" strokeWidth="3" strokeLinecap="round" />
      <path d="M19 7a9 9 0 0 1 0 18" fill="none" stroke="var(--caller)" strokeWidth="3" strokeLinecap="round" />
      <line x1="16" y1="4" x2="16" y2="28" stroke="var(--ink)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="16" cy="16" r="2.6" fill="var(--ink)" />
    </svg>
  );
}

function TopBar({ status, micOk, mode, callState, running, onEnd, onNew }: {
  status: Status | null; micOk?: boolean | null; mode?: 'replay' | 'live'; callState?: CallState | null;
  running?: boolean; onEnd?: () => void; onNew?: () => void;
}) {
  return (
    <header className="topbar">
      <div className="brand"><Logo /><span className="word">CallPilot</span></div>
      <div className="sys" role="status">
        <span className="sys-item" title={`${status?.asr.model ?? ''} · ${status?.asr.location ?? ''}`}><Dot ok={status?.asr.ok} />Whisper</span>
        <span className="sys-item" title={`Scam checks: ${status?.llm.model ?? ''} · ${status?.llm.location ?? ''}`}><Dot ok={status?.llm.ok} />{prettyModel(status?.llm.model)}</span>
        {[status?.assistant, status?.summary].filter((m, i, arr) => m && m.model !== status?.llm.model && arr.findIndex((x) => x?.model === m.model) === i).map((m) => (
          <span key={m!.model} className="sys-item" title={`${m === status?.summary ? 'After-call summaries' : 'Assistant'}: ${m!.model} · ${m!.location}`}><Dot ok={status?.llm.ok} />{prettyModel(m!.model)}</span>
        ))}
        {micOk !== undefined && <span className="sys-item"><Dot ok={!!micOk} />Mic {micOk ? 'on' : micOk === false ? 'blocked' : 'off'}</span>}
        {mode === 'live' && <span className="sys-item"><Dot ok={callState === 'connected'} />Phone {callState ?? 'not invited'}</span>}
        <span className="sys-note"><LockIcon />Processed on this Mac</span>
      </div>
      <div className="actions">
        {onEnd && (running
          ? <><span className={`live-tag ${mode}`}>{mode === 'live' ? 'Live' : 'Replay'}</span><button className="btn end" onClick={onEnd}>End call</button></>
          : <button className="btn" onClick={onNew}>New session</button>)}
      </div>
    </header>
  );
}

function Level({ v, side }: { v: number; side: 'you' | 'caller' }) {
  return <span className={`lvl ${side}`} aria-hidden="true"><i style={{ width: `${Math.min(100, Math.round(Math.sqrt(v) * 100))}%` }} /></span>;
}

function Conversation({ items, flagFor, asr, running, mode, levels, audioInfo, replied }: {
  items: Utterance[]; flagFor: (id: string) => Flag | null; asr: Record<Source, AsrState>; running: boolean; mode: 'replay' | 'live';
  levels: { HOST: number; REMOTE: number }; audioInfo: string; replied: Set<string>;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [items.length, asr.HOST, asr.REMOTE]);
  const busy = (s: AsrState) => s === 'speaking' || s === 'processing';
  return (
    <div className="convo">
      <div className="convo-h" title={audioInfo}>
        <div className="side you"><b>You</b><span className="src-kind">Mac mic</span><Level v={levels.HOST} side="you" /><State s={asr.HOST} /></div>
        <div />
        <div className="side caller"><State s={asr.REMOTE} /><Level v={levels.REMOTE} side="caller" /><span className="src-kind">{mode === 'live' ? 'Phone' : 'Recording'}</span><b>Caller</b></div>
      </div>
      <div className="convo-b">
        {items.length === 0 && (
          <div className="convo-empty">
            {running
              ? <>Press <b>Play caller audio</b>{mode === 'live' ? ' — or invite the phone' : ''}, then speak. Your words land on the left, the caller's on the right.</>
              : 'No speech was captured in this session.'}
          </div>
        )}
        {items.map((u) => {
          const flag = u.source === 'REMOTE' ? flagFor(u.id) : null;
          return (
            <div key={u.id} className={`line ${u.source === 'HOST' ? 'you' : 'caller'}`}>
              <div className="cell">
                {u.source === 'HOST' && <Bubble u={u} flag={null} />}
              </div>
              <div className="spine-cell">
                <span className={`pin ${flag ?? ''}`} />
                <time>{fmtTime(u.start_ms)}</time>
              </div>
              <div className="cell">
                {u.source === 'REMOTE' && <Bubble u={u} flag={flag} replied={replied.has(u.id)} />}
              </div>
            </div>
          );
        })}
        {(busy(asr.HOST) || busy(asr.REMOTE)) && (
          <div className="line ghost">
            <div className="cell">{busy(asr.HOST) && <span className="typing">{asr.HOST === 'speaking' ? 'listening' : 'transcribing'}</span>}</div>
            <div className="spine-cell"><span className="pin faint" /></div>
            <div className="cell">{busy(asr.REMOTE) && <span className="typing">{asr.REMOTE === 'speaking' ? 'listening' : 'transcribing'}</span>}</div>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function State({ s }: { s: AsrState }) {
  const text = { idle: '', speaking: 'listening', processing: 'transcribing', dropped: 'skipped audio', error: 'speech error' }[s];
  return text ? <span className={`st ${s}`}>{text}</span> : null;
}

function Bubble({ u, flag, replied }: { u: Utterance; flag: Flag | null; replied?: boolean }) {
  return (
    <div className={`bubble ${flag ?? ''}`} title={u.id}>
      <p>{u.text}</p>
      {(flag || replied) && (
        <span className="b-tags">
          {flag && <span className={`b-flag ${flag}`}>{FLAG_LABEL[flag]}</span>}
          {replied && <span className="b-flag reply"><SparkIcon />Reply suggested</span>}
        </span>
      )}
    </div>
  );
}

function Evidence({ lines }: { lines: EvidenceLine[] }) {
  return (
    <ul className="evidence">
      {lines.map((l) => (
        <li key={l.id} className={l.source === 'HOST' ? 'you' : 'caller'}>
          <span className="who">{l.source === 'HOST' ? 'You' : 'Caller'} · {l.id}</span>
          <q>{l.text}</q>
        </li>
      ))}
    </ul>
  );
}

function AlertCard({ id, candidate, assessment, byId }: {
  id: string; candidate?: SafetyCandidate; assessment?: SafetyAssessment; byId: Record<string, Utterance>;
}) {
  const cat = CATEGORY_LABEL[assessment?.category ?? candidate?.category ?? 'other'];
  const at = byId[id] ? fmtTime(byId[id].start_ms) : '';
  if (!assessment) {
    return (
      <article className="alert checking">
        <AlertGlyph kind="checking" />
        <div className="a-body">
          <div className="a-top"><p className="a-title">{cat} mentioned</p><time>{at}</time></div>
          <p className="a-action muted">Checking the context…</p>
        </div>
      </article>
    );
  }
  const a = assessment;
  const ask = ASK_TITLE[a.category] ?? 'Sensitive request';
  const title = {
    danger: ask,
    caution: `Be careful — ${ask.charAt(0).toLowerCase()}${ask.slice(1)}`,
    cleared: a.intent === 'warning' ? 'Safe — advice not to share' : 'Safe — no request to you',
    info: `${cat} mentioned, nothing asked`,
    uncertain: `${cat} — intent unclear`,
  }[a.alert];
  return (
    <article className={`alert ${a.alert}`}>
      <AlertGlyph kind={a.alert} />
      <div className="a-body">
      <div className="a-top"><p className="a-title">{title}</p><time>{at}</time></div>
      {a.alert !== 'cleared' && a.alert !== 'info' && <p className="a-action">{cap(a.suggested_action)}</p>}
      <details>
        <summary>Details</summary>
        <p className="a-why">{a.explanation}</p>
        <Evidence lines={a.evidence} />
        <p className="a-meta">
          {a.model} · {a.intent}/{a.severity}{a.latency_ms != null ? ` · ${(a.latency_ms / 1000).toFixed(1)} s` : ''}
          {a.fallback_reason && <> · {a.fallback_reason}</>}
        </p>
      </details>
      </div>
    </article>
  );
}

const BASIS_LABEL: Record<CoachSuggestion['basis'], string> = {
  notes: 'From your notes', conversation: 'From this call', general: 'General safety advice', not_found: 'Not in your notes',
};

function Suggestion({ s }: { s: CoachSuggestion }) {
  const [open, setOpen] = useState<string | null>(null);
  const basis = s.basis ?? (s.found_in_notes ? 'notes' : 'general');
  const para = s.paragraphs.find((p) => p.id === open);
  return (
    <article className={`answer ${s.proactive ? 'auto' : ''}`}>
      <p className="q">{s.proactive ? <><span className="auto-tag"><SparkIcon />Suggested reply</span> Caller asked: “{s.question}”</> : s.question}</p>
      <p className="a">{cap(s.text)}</p>
      <div className="row wrap">
        <span className={`basis ${basis}`}>{BASIS_LABEL[basis]}</span>
        {s.paragraphs.map((p) => (
          <button key={p.id} className={`src ${open === p.id ? 'on' : ''}`} onClick={() => setOpen(open === p.id ? null : p.id)}><DocIcon />{p.id}</button>
        ))}
        {s.evidence.map((e) => <span key={e.id} className="src line" title={e.text}>{e.id}</span>)}
        <span className="a-meta">{s.latency_ms != null ? `${(s.latency_ms / 1000).toFixed(1)} s` : ''}</span>
      </div>
      {para && <blockquote className="para">{para.content}<cite>{para.filename}</cite></blockquote>}
      {s.fallback_reason && <p className="a-meta">Model issue: {s.fallback_reason}</p>}
    </article>
  );
}

function TaskEditor({ task }: { task: TaskProposal }) {
  const [desc, setDesc] = useState(task.description);
  const [due, setDue] = useState(task.due_text);
  const approve = () => api(`/api/tasks/${task.id}/approve`, { method: 'POST', body: JSON.stringify({ description: desc, due_text: due }) });
  const dismiss = () => api(`/api/tasks/${task.id}/dismiss`, { method: 'POST' });
  const locked = task.status !== 'proposed';
  return (
    <div className={`task ${task.status}`}>
      <input value={desc} onChange={(e) => setDesc(e.target.value)} disabled={locked} maxLength={300} aria-label="Task" />
      <div className="row">
        <input className="due" value={due} placeholder="When (as said on the call)" onChange={(e) => setDue(e.target.value)} disabled={locked} maxLength={120} aria-label="Due" />
        {locked
          ? <span className={`done ${task.status}`}>{task.status === 'approved' ? '✓ Approved' : 'Dismissed'}</span>
          : <><button className="btn primary sm" onClick={approve}>Approve</button><button className="btn link" onClick={dismiss}>Dismiss</button></>}
      </div>
      <Evidence lines={task.evidence} />
    </div>
  );
}
