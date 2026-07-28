"""Canonical channel taxonomy for Phase 5 routing.

Maps a Chatwoot inbox channel_type to one of five canonical keys used by
per-agent channel priorities. Channel::TwilioSms -> whatsapp mirrors the agent
service's WhatsApp detection (Twilio-WhatsApp is modelled as TwilioSms)."""

from __future__ import annotations

CANONICAL_CHANNELS: tuple[str, ...] = ("whatsapp", "call", "email", "social", "web")


def canonical_channel(channel_type: str | None) -> str:
    ct = (channel_type or "").lower()
    if "whatsapp" in ct or "twiliosms" in ct:
        return "whatsapp"
    if "voice" in ct:
        return "call"
    if "email" in ct:
        return "email"
    if "facebook" in ct or "instagram" in ct:
        return "social"
    return "web"
