// Host half of the live call. The host's OWN mic track is the only outgoing media.
// The incoming guest track is played by a real <audio> element and handed to the caller for REMOTE capture.

export type CallState =
  | 'waiting' | 'guest-joined' | 'connecting' | 'connected' | 'guest-muted' | 'disconnected' | 'ended' | 'error';

type Signal =
  | { type: 'guest-joined' } | { type: 'answer'; sdp: string } | { type: 'leave' } | { type: 'error'; detail: string }
  | { type: 'ice'; candidate: RTCIceCandidateInit | null } | { type: 'mute'; muted: boolean };

export class HostCall {
  private pc: RTCPeerConnection | null = null;
  private ws: WebSocket | null = null;
  private pendingIce: RTCIceCandidateInit[] = [];

  constructor(
    private readonly micStream: MediaStream,
    private readonly onRemoteStream: (s: MediaStream) => void,
    private readonly onState: (s: CallState, detail?: string) => void,
  ) {}

  connect() {
    this.ws = new WebSocket(`ws://${location.host}/ws/host-signal`);
    this.ws.onmessage = (e) => this.onSignal(JSON.parse(e.data) as Signal);
    this.ws.onclose = () => { if (this.pc?.connectionState !== 'connected') this.onState('ended'); };
    this.onState('waiting');
  }

  private send(msg: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private async onSignal(msg: Signal) {
    switch (msg.type) {
      case 'guest-joined':
        this.onState('guest-joined');
        await this.startOffer();
        break;
      case 'answer':
        await this.pc?.setRemoteDescription({ type: 'answer', sdp: msg.sdp });
        for (const c of this.pendingIce.splice(0)) await this.pc?.addIceCandidate(c).catch(() => {});
        break;
      case 'ice':
        if (!msg.candidate) break;
        if (this.pc?.remoteDescription) await this.pc.addIceCandidate(msg.candidate).catch(() => {});
        else this.pendingIce.push(msg.candidate);
        break;
      case 'mute':
        this.onState(msg.muted ? 'guest-muted' : 'connected');
        break;
      case 'leave':
        this.close(false);
        this.onState('ended');
        break;
      case 'error':
        this.onState('error', msg.detail);
        break;
    }
  }

  private async startOffer() {
    this.pc?.close();
    // Same-network ICE only (hotspot / Tailscale host candidates). No external STUN/TURN.
    const pc = new RTCPeerConnection({ iceServers: [] });
    this.pc = pc;
    for (const track of this.micStream.getAudioTracks()) pc.addTrack(track, this.micStream);
    pc.onicecandidate = (e) => this.send({ type: 'ice', candidate: e.candidate ? e.candidate.toJSON() : null });
    pc.ontrack = ({ track }) => {
      if (track.kind !== 'audio') return;
      this.onRemoteStream(new MediaStream([track]));
    };
    pc.onconnectionstatechange = () => {
      const s = pc.connectionState;
      if (s === 'connecting') this.onState('connecting');
      else if (s === 'connected') this.onState('connected');
      else if (s === 'failed' || s === 'disconnected') this.onState('disconnected');
    };
    this.onState('connecting');
    const offer = await pc.createOffer({ offerToReceiveAudio: true });
    await pc.setLocalDescription(offer);
    this.send({ type: 'offer', sdp: offer.sdp });
  }

  close(notify = true) {
    if (notify) this.send({ type: 'leave' });
    this.pc?.getSenders().forEach((s) => { try { this.pc?.removeTrack(s); } catch { /* closed */ } });
    this.pc?.close();
    this.pc = null;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
    this.ws = null;
  }
}
