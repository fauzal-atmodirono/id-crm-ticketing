// Demo personas for the Phone tab's "Call as" picker. Edit freely.
//
// Only the browser web call uses these: the number travels as the Voice SDK
// `callAs` param and the backend honours it ONLY for a `client:` caller with
// PHONE_WEB_CALL_AS_ENABLED=true (never for a real phone call). All numbers
// are fake; the matching [DEMO] CRM contacts are seeded separately
// (.superpowers/sdd/2026-11-09-phone-caller-context/personas.json).

export interface Persona {
  id: string
  label: string
  /** E.164; '' = no number (the assistant must ask). */
  number: string
  language: 'en' | 'ms' | 'zh' | ''
  description: string
  scriptHint: string
}

// No free-text number (ruling R34): the backend only honours numbers on
// PHONE_WEB_CALL_AS_NUMBERS anyway, and a typed number invites probing.
export const DEFAULT_PERSONA_ID = 'unknown'

export const PERSONAS: Persona[] = [
  {
    id: 'ahmad',
    label: 'Ahmad (known customer)',
    number: '+60000000101',
    language: 'en',
    description: 'Proton X50, plate DMO 1101, dealer Petaling Jaya.',
    scriptHint: 'Ask to book a service for your X50.',
  },
  {
    id: 'nurul',
    label: 'Nurul (Bahasa Melayu, open complaint)',
    number: '+60000000102',
    language: 'ms',
    description: 'Proton Saga, plate DMO 1102, dealer Petaling Jaya, one open complaint case.',
    scriptHint: 'Speak Malay; ask for an update on your complaint, then ask for a person.',
  },
  {
    id: 'lim',
    label: 'Lim (fleet, two cars)',
    number: '+60000000103',
    language: 'zh',
    description: 'Proton X70 DMO 1103 and Proton Persona DMO 1104, dealer Petaling Jaya.',
    scriptHint: 'Speak Mandarin; ask about warranty. The assistant should ask which car.',
  },
  {
    id: 'farid',
    label: 'Farid (breakdown on the highway)',
    number: '+60000000104',
    language: 'en',
    description: 'Proton X70, plate DMO 1105, dealer Petaling Jaya.',
    scriptHint: 'Say your car stopped on the highway. Safety first, then handoff.',
  },
  {
    id: 'new',
    label: 'New caller (not in CRM)',
    number: '+60000000105',
    language: 'en',
    description: 'No contact in the CRM.',
    scriptHint:
      'Give your name, plate DMO 1106 and say you use the Shah Alam service centre (not a configured dealer: stored as text, no label).',
  },
  {
    id: 'unknown',
    label: 'Unknown caller (no number)',
    number: '',
    language: '',
    description: 'No caller ID reaches the assistant.',
    scriptHint:
      'The assistant asks for your mobile number and name: say e.g. "zero zero zero zero zero zero zero one zero one" and "Ahmad". A wrong name is treated as a new caller.',
  },
]
