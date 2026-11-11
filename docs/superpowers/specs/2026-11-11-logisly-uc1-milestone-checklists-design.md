# Logisly UC1: milestone checklists for the driver follow-up agent

## Why

Logisly sent their real follow-up material (11 Nov 2026): the six order
milestones, the per-milestone SOP checklist, four WhatsApp screenshots and
seven call recordings. Their current WhatsApp bot works the checklist as a
script: it asked "sudah sampai lokasi muat?" every 40 minutes from 02:00 to
07:40, ignored Timemark photos that showed plate, time and place, ignored
"sya blum bisa login di aplikasi" and answered "yg mna ya?" with a canned line.
Their human agents on the phone are short and practical: confirm plate, ask
position and ETA, ask for the surat jalan photo, answer the driver's own
question (cargo weight from the DO, the DTS barcode, where the load point is).

The demo must show the process the updated deck promises (slides 16 to 18):
**check data first, ask the driver only what no system knows, read photos as
evidence, send each "not OK" item to the right team.**

## Goals

- Each trip moves through six milestones: `assigned`, `to_pickup`,
  `at_pickup`, `in_transit`, `at_drop`, `unloaded`. A milestone is set by
  `gps`, `app` or `manual` (operator); the console offers all three.
- Each milestone (except `to_pickup`) has a checklist taken from Logisly's
  SOP. Every item has a source: `system` (filled from trip facts), `photo`
  (driver sends a photo, the agent reads it) or `driver` (asked).
- On a milestone change the system items are filled first. The driver gets
  one opening message that covers only the photo/driver items still open and
  any fix the driver must do (e.g. install the driver app). Nothing open and
  nothing to tell the driver: no message at all.
- The agent ticks several items from one reply, never re-asks an answered
  item, asks at most two items per message, and answers the driver's own
  question first from the trip data.
- A "not OK" item (from a system check or from the agent) becomes a routed
  ticket on the conversation: private note with the item, evidence and trip,
  labels `logisly-checklist` + `team-<team>`, priority high.
- When a milestone's checklist is complete the conversation gets a private
  note summarising it.
- Quiet hours 22:00 to 05:00 WIB: a milestone change records the checklist but
  defers the driver message (console can force it).
- The console shows each trip's milestone stepper and checklist with source
  and status per item.

## Non-goals

- Real Logisly, Shopee, GPS or driver-app integration. Trip facts stay in the
  in-memory `TripStore`; a real adapter replaces it later.
- Changing the voice call (Gemini Live) tools. Calls keep using
  `update_trip_status`.
- UC2 (pricing, on hold) and UC3.

## Anti-goals

- The agent asks a question whose answer is in the trip facts.
- More than one unanswered agent message per milestone (no polling).
- Real driver names, phone numbers or screenshots in the repo. Seed data is
  fictional, modelled on the samples.

## Checklist (from Logisly's SOP sheet; team = who gets a "not OK")

| Milestone | Item id | Source | OK when | Not OK team |
|---|---|---|---|---|
| assigned | `driver_plate` | system | assigned plate = registered plate | alloc |
| assigned | `locations` | system | driver app shows the order's load and drop points | alloc |
| assigned | `shopee_id` | system | Shopee driver ID active (Shopee orders only) | alloc |
| assigned | `unit_empty` | driver | unit is empty, or driver gives drop point, finish time and cargo | — (recorded) |
| at_pickup | `unit_photo` | photo | photo shows the truck, plate matches, timestamp present | alloc |
| at_pickup | `onsite` | system | Shopee onsite status set (Shopee orders) | — (driver told to tap "onsite") |
| at_pickup | `load_eta` | driver | driver gives a loading time | warehouse (no queue ticket) |
| at_pickup | `driver_app` | system | driver app active | — (driver gets install link) |
| at_pickup | `do_in_app` | system | DO in driver app | alloc |
| in_transit | `surat_jalan` | photo | surat jalan photo received | alloc |
| in_transit | `drop_address` | system | drop address matches PF | alloc (shipper/sales/TS informed) |
| in_transit | `eta_drop` | driver | driver gives arrival date/time at drop | — |
| in_transit | `trip_ok` | system | GPS on track and ETA within RAD | ct (if late; driver asked about kendala) |
| in_transit | `kendala` | driver | no problem, or problem described | ct (if replacement unit needed) |
| at_drop | `unit_photo` | photo | truck at drop point, plate and timestamp | alloc |
| at_drop | `unloading_registered` | system | unloading registered in Shopee app | — (driver told to tap "arrive") |
| at_drop | `unload_eta` | driver | driver gives an unloading time | warehouse |
| unloaded | `unload_done` | driver | unloading finished | — |
| unloaded | `pod` | photo | signed POD / surat jalan visible | mm |
| unloaded | `pod_uploaded` | system | POD uploaded in driver app | — (driver reminded) |

Shopee-only items are skipped for trips whose `shipper_kind` is not `shopee`.

## Decisions taken without the user (my calls)

- "Setelah Muat" items open the `in_transit` checklist; "Dalam Perjalanan"
  repeats use the existing GPS-anomaly endpoint.
- Teams are labels on the same Chatwoot conversation, not separate inboxes.
- Install link and DTS numbers are fictional constants in the seed.
