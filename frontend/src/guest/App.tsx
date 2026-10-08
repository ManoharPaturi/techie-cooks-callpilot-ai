// Guest endpoint: consent, join, mute, leave and audio playback ONLY. No AI, transcripts or host data.
import { useEffect, useRef, useState } from 'react';

type Phase = 'consent' | 'joining' | 'waiting' | 'connected' | 'ended' | 'error';
type Signal =
  | { type: 'ready' } | { type: 'offer'; sdp: string } | { type: 'leave' } | { type: 'error'; detail: string }
  | { type: 'ice'; candidate: RTCIceCandidateInit | null };

export default function App() {
  const [phase, setPhase] = useState<Phase>('consent');
  const [detail, setDetail] = useState('');
  const [muted, setMuted] = useState(false);
  const [needsTap, setNeedsTap] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const pc = useRef<RTCPeerConnection | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const mic = useRef<MediaStream | null>(null);
  const pendingIce = useRef<RTCIceCandidateInit[]>([]);
  const token = location.pathname.split('/').filter(Boolean).pop() ?? '';

  const send = (m: object) => { if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(m)); };

  const cleanup = () => {
    pc.current?.close(); pc.current = null;
    mic.current?.getTracks().forEach((t) => t.stop()); mic.current = null;
    if (audio.current) audio.current.srcObject = null;
    if (ws.current && ws.current.readyState <= WebSocket.OPEN) ws.current.close();
    ws.current = null;
  };
  useEffect(() => cleanup, []);

  const onOffer = async (sdp: string) => {
    const conn = new RTCPeerConnection({ iceServers: [] });
    pc.current = conn;
    mic.current!.getAudioTracks().forEach((t) => conn.addTrack(t, mic.current!)); // only our own mic
    conn.onicecandidate = (e) => send({ type: 'ice', candidate: e.candidate ? e.candidate.toJSON() : null });
    conn.ontrack = async ({ track }) => {
      if (track.kind !== 'audio' || !audio.current) return;
      audio.current.srcObject = new MediaStream([track]);
      try { await audio.current.play(); setNeedsTap(false); } catch { setNeedsTap(true); }
    };
    conn.onconnectionstatechange = () => {
      if (conn.connectionState === 'connected') setPhase('connected');
      if (conn.connectionState === 'failed') { setDetail('Could not connect audio on this network.'); setPhase('error'); }
    };
    await conn.setRemoteDescription({ type: 'offer', sdp });
    for (const c of pendingIce.current.splice(0)) await conn.addIceCandidate(c).catch(() => {});
    const answer = await conn.createAnswer();
    await conn.setLocalDescription(answer);
    send({ type: 'answer', sdp: answer.sdp });
  };

  const join = async () => {
    setPhase('joining');
    try {
      mic.current = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false,
      });
    } catch (e) {
      setDetail(`Microphone permission is needed to join: ${(e as Error).message}`);
      setPhase('error');
      return;
    }
    const sock = new WebSocket(`wss://${location.host}/ws/guest-signal/${encodeURIComponent(token)}`);
    ws.current = sock;
    sock.onmessage = async (e) => {
      const msg = JSON.parse(e.data) as Signal;
      if (msg.type === 'ready') setPhase('waiting');
      else if (msg.type === 'offer') await onOffer(msg.sdp);
      else if (msg.type === 'ice' && msg.candidate) {
        if (pc.current?.remoteDescription) await pc.current.addIceCandidate(msg.candidate).catch(() => {});
        else pendingIce.current.push(msg.candidate);
      } else if (msg.type === 'leave') { cleanup(); setPhase('ended'); }
      else if (msg.type === 'error') { setDetail(msg.detail); setPhase('error'); cleanup(); }
    };
    sock.onclose = () => setPhase((p) => (p === 'connected' || p === 'waiting' || p === 'joining' ? 'ended' : p));
  };

  const toggleMute = () => {
    const next = !muted;
    mic.current?.getAudioTracks().forEach((t) => { t.enabled = !next; });
    setMuted(next);
    send({ type: 'mute', muted: next });
  };

  const leave = () => { send({ type: 'leave' }); cleanup(); setPhase('ended'); };

  return (
    <main className="guest">
      <div className="g-brand">
        <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M13 7a9 9 0 0 0 0 18" fill="none" stroke="#2d4fe0" strokeWidth="3" strokeLinecap="round" /><path d="M19 7a9 9 0 0 1 0 18" fill="none" stroke="#e39400" strokeWidth="3" strokeLinecap="round" /><line x1="16" y1="4" x2="16" y2="28" stroke="#0f1420" strokeWidth="2" strokeLinecap="round" /><circle cx="16" cy="16" r="2.6" fill="#0f1420" /></svg>
        <span>CallPilot</span>
      </div>
      <h1>{phase === 'connected' ? 'You’re on the call' : phase === 'ended' ? 'Call ended' : 'Join the call'}</h1>
      {phase === 'consent' && (
        <>
          <p>
            You are joining an audio call from this browser. <b>The host's Mac transcribes this call locally</b> (on
            their computer, not in the cloud) and uses a private AI assistant that only the host can see.
            Nothing is shown or played to you except the host's voice. You can mute or leave at any time.
          </p>
          <button className="big" onClick={join}>Agree and join</button>
        </>
      )}
      {phase === 'joining' && <p className="status">Starting microphone…</p>}
      {phase === 'waiting' && <p className="status">Connecting to the host…</p>}
      {phase === 'connected' && <div className="live"><span className="ring" /><p className="status ok">Connected{muted ? ' · you’re muted' : ''}</p></div>}
      {phase === 'ended' && <p className="status">Call ended. You can close this page.</p>}
      {phase === 'error' && <p className="status bad">{detail || 'Something went wrong.'}</p>}

      <audio ref={audio} autoPlay playsInline />
      {needsTap && <button className="big" onClick={async () => { await audio.current?.play(); setNeedsTap(false); }}>🔊 Tap to enable call audio</button>}

      {(phase === 'waiting' || phase === 'connected') && (
        <div className="controls">
          <button className={muted ? 'big muted' : 'big'} onClick={toggleMute}>{muted ? 'Unmute' : 'Mute'}</button>
          <button className="big leave" onClick={leave}>Leave</button>
        </div>
      )}
    </main>
  );
}
