// Audio engine: ONE AudioContext, one shared PCM worklet implementation, separately registered
// HOST and REMOTE captures. Replay and live WebRTC differ only in the upstream AudioNode.
import workletUrl from './pcm-tap.worklet.js?url';

export type Source = 'HOST' | 'REMOTE';
export type Origin = 'replay' | 'live';

const HEADER_BYTES = 12; // uint32 seq + float64 t_ms
const MAX_BUFFERED_BYTES = 512 * 1024;

export class PcmTap {
  readonly node: AudioWorkletNode;
  captureId: string | null = null;
  peak = 0;
  framesSent = 0;
  framesDropped = 0;
  closed = false;
  private seq = 0;

  private constructor(
    readonly source: Source,
    readonly origin: Origin,
    readonly sampleRate: number,
    node: AudioWorkletNode,
    private readonly ws: WebSocket,
    private readonly t0: number,
  ) {
    this.node = node;
  }

  /** Register the capture with the backend BEFORE any audio flows, then wire the worklet to the sender. */
  static async create(engine: AudioEngine, sessionId: string, source: Source, origin: Origin): Promise<PcmTap> {
    const ctx = engine.ctx;
    const ws = new WebSocket(`ws://${location.host}/ws/audio`);
    ws.binaryType = 'arraybuffer';
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('audio socket failed to open'));
    });
    ws.send(JSON.stringify({ type: 'register', session_id: sessionId, source, origin, sample_rate: ctx.sampleRate }));
    const reply = await new Promise<{ type: string; capture_id?: string; detail?: string }>((resolve, reject) => {
      ws.onmessage = (e) => resolve(JSON.parse(e.data));
      ws.onclose = () => reject(new Error('audio socket closed during registration'));
    });
    if (reply.type !== 'registered' || !reply.capture_id) throw new Error(reply.detail ?? 'registration rejected');

    const node = new AudioWorkletNode(ctx, 'pcm-tap', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 2 });
    const tap = new PcmTap(source, origin, ctx.sampleRate, node, ws, engine.t0);
    tap.captureId = reply.capture_id;
    ws.onmessage = null;
    ws.onclose = () => { tap.closed = true; };
    node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; t: number; peak: number }>) => tap.send(e.data);
    // Worklet output goes ONLY to a silent sink (keeps the graph pulling without duplicate audio).
    node.connect(engine.silentSink);
    return tap;
  }

  private send({ pcm, t, peak }: { pcm: ArrayBuffer; t: number; peak: number }) {
    this.peak = Math.max(peak, this.peak * 0.85);
    if (this.closed || this.ws.readyState !== WebSocket.OPEN) return;
    if (this.ws.bufferedAmount > MAX_BUFFERED_BYTES) { this.framesDropped++; return; } // bounded, never unbounded
    const frame = new ArrayBuffer(HEADER_BYTES + pcm.byteLength);
    const view = new DataView(frame);
    view.setUint32(0, ++this.seq, true);
    view.setFloat64(4, Math.max(0, (t - this.t0) * 1000), true);
    new Uint8Array(frame, HEADER_BYTES).set(new Uint8Array(pcm));
    this.ws.send(frame);
    this.framesSent++;
  }

  close() {
    this.closed = true;
    this.node.port.onmessage = null;
    try { this.node.disconnect(); } catch { /* already disconnected */ }
    if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) this.ws.close();
  }
}

export class AudioEngine {
  ctx!: AudioContext;
  silentSink!: GainNode;
  t0 = 0;
  micStream: MediaStream | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private replaySources = new WeakMap<HTMLMediaElement, MediaElementAudioSourceNode>();
  taps: Partial<Record<Source, PcmTap>> = {};

  /** Must run inside a user gesture (Start button) so the context may resume. */
  async init() {
    // Normal-rate context: playback quality is preserved; the backend does anti-aliased resampling to 16 kHz
    // using the ACTUAL rate reported here.
    this.ctx = new AudioContext({ latencyHint: 'interactive' });
    await this.ctx.audioWorklet.addModule(workletUrl);
    await this.ctx.resume();
    this.t0 = this.ctx.currentTime;
    this.silentSink = this.ctx.createGain();
    this.silentSink.gain.value = 0;
    this.silentSink.connect(this.ctx.destination);
  }

  get sampleRate() { return this.ctx?.sampleRate ?? 0; }

  async startMic(sessionId: string, origin: Origin, deviceId?: string): Promise<PcmTap> {
    this.micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      },
      video: false,
    });
    const tap = await PcmTap.create(this, sessionId, 'HOST', origin);
    this.micSource = this.ctx.createMediaStreamSource(this.micStream);
    this.micSource.connect(tap.node); // NOT to destination: no sidetone
    this.taps.HOST = tap;
    return tap;
  }

  /** Remote source = replay <audio>. createMediaElementSource reroutes playback, so it must ALSO go to
   * the destination to stay audible. Created once per element. */
  async attachReplay(sessionId: string, audioEl: HTMLAudioElement): Promise<PcmTap> {
    let src = this.replaySources.get(audioEl);
    if (!src) {
      src = this.ctx.createMediaElementSource(audioEl);
      src.connect(this.ctx.destination); // caller is AUDIBLE in the host's headphones
      this.replaySources.set(audioEl, src);
    }
    if (!this.taps.REMOTE || this.taps.REMOTE.closed) {
      this.taps.REMOTE = await PcmTap.create(this, sessionId, 'REMOTE', 'replay');
      src.connect(this.taps.REMOTE.node); // REMOTE capture path
    }
    return this.taps.REMOTE;
  }

  private liveSource: MediaStreamAudioSourceNode | null = null;

  /** Live mode: the remote WebRTC stream feeds the SAME REMOTE worklet used by replay, with a fresh capture
   * registration (origin=live). Playback is owned by a real <audio> element, so this branch goes ONLY to the
   * worklet -> silent sink, never to the destination (the caller would be heard twice). */
  async attachLiveRemote(sessionId: string, stream: MediaStream): Promise<PcmTap> {
    this.detachLiveRemote();
    this.taps.REMOTE = await PcmTap.create(this, sessionId, 'REMOTE', 'live');
    this.liveSource = this.ctx.createMediaStreamSource(stream);
    this.liveSource.connect(this.taps.REMOTE.node);
    return this.taps.REMOTE;
  }

  detachLiveRemote() {
    try { this.liveSource?.disconnect(); } catch { /* already disconnected */ }
    this.liveSource = null;
    this.taps.REMOTE?.close();
    delete this.taps.REMOTE;
  }

  async stop() {
    for (const tap of Object.values(this.taps)) tap?.close();
    this.taps = {};
    this.micStream?.getTracks().forEach((t) => t.stop());
    this.micStream = null;
    this.micSource = null;
    if (this.ctx && this.ctx.state !== 'closed') await this.ctx.close();
  }
}
