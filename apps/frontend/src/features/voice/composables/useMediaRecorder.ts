import { ref } from 'vue';

const PREFERRED_MIMETYPES = [
  'audio/ogg;codecs=opus',
  'audio/webm;codecs=opus',
  'audio/webm',
];

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  for (const mt of PREFERRED_MIMETYPES) {
    if (MediaRecorder.isTypeSupported(mt)) return mt;
  }
  return undefined;
}

export interface MediaRecorderHandle {
  isRecording: import('vue').Ref<boolean>;
  error: import('vue').Ref<string | null>;
  start: () => Promise<void>;
  stop: () => Promise<Blob>;
}

export function useMediaRecorder(): MediaRecorderHandle {
  const isRecording = ref<boolean>(false);
  const error = ref<string | null>(null);
  let recorder: MediaRecorder | null = null;
  let chunks: Blob[] = [];
  let activeStream: MediaStream | null = null;
  let pendingResolve: ((blob: Blob) => void) | null = null;
  let pendingReject: ((reason: Error) => void) | null = null;

  async function start(): Promise<void> {
    error.value = null;
    if (isRecording.value) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      error.value = 'Microphone API not available in this browser.';
      throw new Error(error.value);
    }
    const mimeType = pickMimeType();
    if (!mimeType) {
      error.value = 'No supported audio recording format in this browser (try Chrome or Firefox).';
      throw new Error(error.value);
    }

    activeStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    recorder = new MediaRecorder(activeStream, { mimeType });

    recorder.addEventListener('dataavailable', (e: BlobEvent) => {
      if (e.data.size > 0) chunks.push(e.data);
    });
    recorder.addEventListener('stop', () => {
      const blob = new Blob(chunks, { type: mimeType });
      activeStream?.getTracks().forEach((t) => t.stop());
      activeStream = null;
      recorder = null;
      isRecording.value = false;
      pendingResolve?.(blob);
      pendingResolve = null;
      pendingReject = null;
    });
    recorder.addEventListener('error', (e: Event) => {
      pendingReject?.(new Error(`MediaRecorder error: ${(e as ErrorEvent).message}`));
      pendingResolve = null;
      pendingReject = null;
    });

    recorder.start();
    isRecording.value = true;
  }

  function stop(): Promise<Blob> {
    return new Promise<Blob>((resolve, reject) => {
      if (!recorder || recorder.state === 'inactive') {
        reject(new Error('Not recording'));
        return;
      }
      pendingResolve = resolve;
      pendingReject = reject;
      recorder.stop();
    });
  }

  return { isRecording, error, start, stop };
}
