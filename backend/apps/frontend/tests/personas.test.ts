// Brief 6: the Chat/Voice persona picker sends a number ONLY when one is
// chosen. Run: node --test tests/ (Node >= 22.6 strips the types).
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  ANONYMOUS_PERSONA_ID,
  CHAT_PERSONAS,
  chatPersonaNumber,
  personaFields,
} from '../src/features/phone/personas.ts'

test('anonymous is the first option and the default, and has no number', () => {
  assert.equal(CHAT_PERSONAS[0]?.id, ANONYMOUS_PERSONA_ID)
  assert.equal(chatPersonaNumber(ANONYMOUS_PERSONA_ID), '')
})

test('only personas with a number are offered besides anonymous', () => {
  for (const p of CHAT_PERSONAS.slice(1)) assert.match(p.number, /^\+600000001\d\d$/)
  assert.ok(CHAT_PERSONAS.some((p) => p.id === 'ahmad'))
  assert.ok(!CHAT_PERSONAS.some((p) => p.id === 'unknown'))
})

test('a chosen persona sends persona_phone; none sends nothing', () => {
  assert.deepEqual(personaFields(chatPersonaNumber('ahmad')), { persona_phone: '+60000000101' })
  assert.deepEqual(personaFields(''), {})
  assert.deepEqual(personaFields(chatPersonaNumber('no-such-id')), {})
})
