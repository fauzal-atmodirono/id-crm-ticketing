// Console session ids (Chat / Voice tabs). crypto.randomUUID only exists in a
// secure context (HTTPS / localhost); a plain-HTTP dev host falls back to
// getRandomValues, then Math.random -- still a v4-shaped id, and never a
// server-owned prefix (the backend refuses those without its key).
// No imports: tests/sessionId.test.ts runs this under plain Node.

interface CryptoLike {
  randomUUID?: () => string
  getRandomValues?: <T extends ArrayBufferView>(array: T) => T
}

export function uuidV4(c: CryptoLike | undefined = globalThis.crypto as CryptoLike | undefined): string {
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const bytes = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') c.getRandomValues(bytes)
  else for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function newSessionId(prefix: string): string {
  return `${prefix}-${uuidV4()}`
}
