// Fix round 2: console session ids are UUIDs, with a fallback when
// crypto.randomUUID is unavailable (non-secure context).
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { newSessionId, uuidV4 } from '../src/features/chat/sessionId.ts'

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

test('uses crypto.randomUUID when available', () => {
  assert.equal(uuidV4({ randomUUID: () => 'fixed-uuid' }), 'fixed-uuid')
})

test('falls back to getRandomValues, then to Math.random, as a v4 uuid', () => {
  const grv = {
    getRandomValues: <T extends ArrayBufferView>(a: T): T => {
      new Uint8Array(a.buffer).fill(0xab)
      return a
    },
  }
  assert.match(uuidV4(grv), V4)
  assert.match(uuidV4(undefined), V4)
  assert.notEqual(uuidV4(undefined), uuidV4(undefined))
})

test('prefixes the session id', () => {
  assert.match(newSessionId('sim'), /^sim-/)
  assert.match(newSessionId('voice'), /^voice-/)
})
