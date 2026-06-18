import { ref, shallowRef, type Ref, type ShallowRef } from 'vue';

type CaptureStatus = 'idle' | 'listening' | 'speaking' | 'silence' | 'finalizing';

interface UseVoiceCaptureOptions {
  // Auto-stop tuning. Speech must be detected for at least `speechMinMs`
  // before the trailing-silence timer can fire after `silenceMs`.
  silenceRms?: number;
  silenceMs?: number;
  speechMinMs?: number;
  maxRecordingMs?: number;
  targetSampleRate?: number;
}

const DEFAULTS = {
  silenceRms: 0.018,
  silenceMs: 1200,
  speechMinMs: 350,
  maxRecordingMs: 30_000,
  targetSampleRate: 16_000,
};

const PREFERRED_MIMETYPES = [
  'audio/webm;codecs=opus',
  'audio/ogg;codecs=opus',
  'audio/webm',
];

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  for (const mt of PREFERRED_MIMETYPES) {
    if (MediaRecorder.isTypeSupported(mt)) return mt;
  }
  return undefined;
}

export interface VoiceCaptureHandle {
  status: Ref<CaptureStatus>;
  isActive: Ref<boolean>;
  hasSpeech: Ref<boolean>;
  level: Ref<number>;
  error: Ref<string | null>;
  analyser: ShallowRef<AnalyserNode | null>;
  start: (onComplete: (wav: Blob) => void) => Promise<void>;
  stop: () => Promise<void>;
  cancel: () => void;
}

export function useVoiceCapture(opts: UseVoiceCaptureOptions = {}): VoiceCaptureHandle {
  const cfg = { ...DEFAULTS, ...opts };

  const status = ref<CaptureStatus>('idle');
  const isActive = ref<boolean>(false);
  const hasSpeech = ref<boolean>(false);
  const level = ref<number>(0);
  const error = ref<string | null>(null);
  const analyser = shallowRef<AnalyserNode | null>(null);

  let stream: MediaStream | null = null;
  let audioCtx: AudioContext | null = null;
  let sourceNode: MediaStreamAudioSourceNode | null = null;
  let recorder: MediaRecorder | null = null;
  let chunks: Blob[] = [];
  let rafHandle: number | null = null;
  let speechStartAt = 0;
  let silenceStartAt = 0;
  let recordingStartedAt = 0;
  let completion: ((wav: Blob) => void) | null = null;
  let finalizing = false;
  let timeBuf: Uint8Array | null = null;

  function reset(): void {
    chunks = [];
    speechStartAt = 0;
    silenceStartAt = 0;
    recordingStartedAt = 0;
    hasSpeech.value = false;
    level.value = 0;
    finalizing = false;
  }

  function teardown(): void {
    if (rafHandle != null) {
      cancelAnimationFrame(rafHandle);
      rafHandle = null;
    }
    if (recorder && recorder.state !== 'inactive') {
      try { recorder.stop(); } catch { /* noop */ }
    }
    recorder = null;
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
    sourceNode?.disconnect();
    sourceNode = null;
    if (audioCtx) {
      audioCtx.close().catch(() => undefined);
      audioCtx = null;
    }
    analyser.value = null;
    isActive.value = false;
    status.value = 'idle';
  }

  function tick(): void {
    if (!analyser.value || !timeBuf) return;
    analyser.value.getByteTimeDomainData(timeBuf);

    // RMS of centered samples (0..1).
    let sum = 0;
    const buf = timeBuf;
    for (let i = 0; i < buf.length; i++) {
      const v = ((buf[i] ?? 128) - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / buf.length);
    // Smooth slightly so the meter doesn't jitter.
    level.value = level.value * 0.6 + rms * 0.4;

    const now = performance.now();

    if (rms >= cfg.silenceRms) {
      silenceStartAt = 0;
      if (!hasSpeech.value) {
        if (speechStartAt === 0) speechStartAt = now;
        if (now - speechStartAt >= cfg.speechMinMs) {
          hasSpeech.value = true;
          status.value = 'speaking';
        }
      } else {
        status.value = 'speaking';
      }
    } else {
      if (hasSpeech.value) {
        if (silenceStartAt === 0) silenceStartAt = now;
        status.value = 'silence';
        if (now - silenceStartAt >= cfg.silenceMs) {
          void finalize();
          return;
        }
      } else {
        // No speech yet — keep waiting.
        speechStartAt = 0;
      }
    }

    if (recordingStartedAt && now - recordingStartedAt >= cfg.maxRecordingMs) {
      void finalize();
      return;
    }

    rafHandle = requestAnimationFrame(tick);
  }

  async function finalize(): Promise<void> {
    if (finalizing) return;
    finalizing = true;
    status.value = 'finalizing';
    if (rafHandle != null) {
      cancelAnimationFrame(rafHandle);
      rafHandle = null;
    }

    const sourceMime = recorder?.mimeType ?? 'audio/webm';
    let blob: Blob | null = null;
    try {
      blob = await stopRecorder();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    }

    // Snapshot completion before teardown so any consumer-side error doesn't
    // leak state across captures.
    const cb = completion;
    completion = null;

    if (blob && blob.size > 0) {
      try {
        const wav = await encodeWavFromBlob(blob, sourceMime, cfg.targetSampleRate);
        cb?.(wav);
      } catch (e) {
        error.value = e instanceof Error ? e.message : 'Failed to encode audio';
      }
    }

    teardown();
  }

  function stopRecorder(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      if (!recorder) {
        reject(new Error('Recorder not initialized'));
        return;
      }
      if (recorder.state === 'inactive') {
        resolve(new Blob(chunks, { type: recorder.mimeType }));
        return;
      }
      const r = recorder;
      r.addEventListener(
        'stop',
        () => resolve(new Blob(chunks, { type: r.mimeType })),
        { once: true },
      );
      r.addEventListener(
        'error',
        (e: Event) => reject(new Error(`MediaRecorder error: ${(e as ErrorEvent).message}`)),
        { once: true },
      );
      try { r.stop(); } catch (e) { reject(e instanceof Error ? e : new Error(String(e))); }
    });
  }

  async function start(onComplete: (wav: Blob) => void): Promise<void> {
    if (isActive.value) return;
    error.value = null;
    reset();

    if (!navigator.mediaDevices?.getUserMedia) {
      error.value = 'Microphone API not available in this browser.';
      throw new Error(error.value);
    }
    const mimeType = pickMimeType();
    if (!mimeType) {
      error.value = 'No supported audio recording format in this browser.';
      throw new Error(error.value);
    }

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (e) {
      error.value =
        e instanceof Error && e.name === 'NotAllowedError'
          ? 'Microphone permission denied.'
          : e instanceof Error
            ? e.message
            : 'Failed to access microphone.';
      throw new Error(error.value);
    }

    audioCtx = new AudioContext();
    sourceNode = audioCtx.createMediaStreamSource(stream);
    const node = audioCtx.createAnalyser();
    node.fftSize = 1024;
    node.smoothingTimeConstant = 0.4;
    sourceNode.connect(node);
    analyser.value = node;
    timeBuf = new Uint8Array(node.fftSize);

    completion = onComplete;
    recorder = new MediaRecorder(stream, { mimeType });
    recorder.addEventListener('dataavailable', (e: BlobEvent) => {
      if (e.data.size > 0) chunks.push(e.data);
    });
    recorder.start();

    recordingStartedAt = performance.now();
    isActive.value = true;
    status.value = 'listening';
    rafHandle = requestAnimationFrame(tick);
  }

  async function stop(): Promise<void> {
    if (!isActive.value) return;
    await finalize();
  }

  function cancel(): void {
    completion = null;
    teardown();
    reset();
  }

  return { status, isActive, hasSpeech, level, error, analyser, start, stop, cancel };
}

// --- WAV encoding ----------------------------------------------------------

async function encodeWavFromBlob(
  src: Blob,
  _sourceMime: string,
  targetSampleRate: number,
): Promise<Blob> {
  const arr = await src.arrayBuffer();
  // OfflineAudioContext could be used for higher-quality resampling, but a
  // regular AudioContext decode keeps us inside one path that handles the
  // browser's native webm/opus and ogg/opus containers identically.
  const decodeCtx = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await decodeCtx.decodeAudioData(arr.slice(0));
  } finally {
    decodeCtx.close().catch(() => undefined);
  }

  const mono = mixToMono(decoded);
  const resampled =
    decoded.sampleRate === targetSampleRate
      ? mono
      : linearResample(mono, decoded.sampleRate, targetSampleRate);

  return encodeWav(resampled, targetSampleRate);
}

function mixToMono(buffer: AudioBuffer): Float32Array {
  if (buffer.numberOfChannels === 1) return buffer.getChannelData(0).slice();
  const out = new Float32Array(buffer.length);
  const gain = 1 / buffer.numberOfChannels;
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i++) {
      out[i] = (out[i] ?? 0) + (data[i] ?? 0) * gain;
    }
  }
  return out;
}

function linearResample(samples: Float32Array, srcRate: number, dstRate: number): Float32Array {
  if (srcRate === dstRate) return samples;
  const ratio = srcRate / dstRate;
  const newLength = Math.floor(samples.length / ratio);
  const out = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const idx = i * ratio;
    const i0 = Math.floor(idx);
    const i1 = Math.min(i0 + 1, samples.length - 1);
    const frac = idx - i0;
    const a = samples[i0] ?? 0;
    const b = samples[i1] ?? 0;
    out[i] = a * (1 - frac) + b * frac;
  }
  return out;
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, 'data');
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
}
