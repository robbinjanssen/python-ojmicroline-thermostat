# Device protocol of WG4-series thermostats

Maintainer notes on how a WG4-series thermostat talks to OJ's device gateway,
as implemented in [`ojmicroline_thermostat/gateway`](../ojmicroline_thermostat/gateway).
There is no published specification. The field numbers are as observed by
[peskyAdmin/schluter-local](https://github.com/peskyAdmin/schluter-local)
(MIT), whose protocol notes this document is adapted from. The tests in
[`tests/test_gateway_wg4.py`](../tests/test_gateway_wg4.py) pin the behaviour
with frames captured from real thermostats.

None of this has been confirmed with this library on real hardware yet.

Where a point is marked as reported, it comes from one WG4 thermostat with
firmware 1012M201: a capture of a single session with OJ's gateway, and a
gateway that answered the thermostat itself, both described by its owner in
[pull request 478 of the Home Assistant integration](https://github.com/robbinjanssen/home-assistant-ojmicroline-thermostat/pull/478).

## Transport

- The thermostat connects to `na-thermos.ojelectronics.com` on TCP port 9533
  with TLS 1.2. It does not check the certificate of the server.
- OJ's gateway presents a self-signed certificate. `WG4_NORTH_AMERICA` holds
  its SHA-256 fingerprint, read on 9 November 2026 from a certificate that is
  valid until 2114.
- A frame is the byte `0x08`, the length of the message as a varint, and a
  protobuf message.
- Field 1 of a message is a sequence number. An acknowledgement repeats that
  number with field 3 set to 1. See [Sequence numbers](#sequence-numbers).
- Field 5 is 1 on most messages in both directions. What it means is not
  known.
- A thermostat keeps the connection it has when the DNS record changes. It
  looks the name up again when it connects anew. Reported: switching the
  thermostat off and on is the reliable way to get there, and a factory reset
  is not needed.

## From the thermostat

| Message | Meaning | Answer of the gateway |
| --- | --- | --- |
| Has field 6 | Login. 6.1 is the email address of the account, 6.2 the serial number, 6.3 a number that is not used | Acknowledgement with 4 = 1 and 12 = {1: server time} |
| 2 = 6 | Upload of the schedule and settings | Acknowledgement, then the support answer (field 18) |
| 2 = 4 | Full upload after login: 7 device info, 8 schedule, 9 state | Acknowledgement, then the configuration answer (field 10) |
| Field 9, no field 2 | Someone changed the thermostat at the wall | Acknowledgement |
| Field 7, no field 2 | The device info changed | Acknowledgement |
| Field 15 | Heartbeat, about once a minute: 15.1 the floor temperature in 1/100 °C, 15.2 heating (1 or 0) | Acknowledgement |
| Fields 1 and 3 only | The thermostat acknowledges a message it received | Nothing |

The gateway requires the login as the first message, within 30 seconds, and
closes the connection on a second login. A thermostat that stays silent for
180 seconds is disconnected.

## Sequence numbers

Each side numbers the messages it starts, and an acknowledgement repeats the
number of the message it answers.

Reported from the captured session, in the order OJ's gateway sent them:

| Message of OJ's gateway | Sequence number |
| --- | --- |
| Acknowledgement of the login | 1 |
| Acknowledgement of the upload with 2 = 6 | 2 |
| Support answer (field 18) | 0 |
| Acknowledgement of the upload with 2 = 4 | 3 |
| Configuration answer (field 10) | 1 |
| Acknowledgements of the first two heartbeats | 4 and 5 |

So the acknowledgements carry the thermostat's numbers, and OJ's gateway
counted its own two messages from 0. How its numbers go on in a longer
session, or for a command from the vendor's app, is not known.

## State (field 9)

- 9.1: the time of the change, in seconds since 2000-01-01 UTC.
- 9.2: the regulation mode. 1 schedule, 2 temporary hold, 3 permanent hold,
  4 vacation. These are the `RegulationMode` values of the cloud API.
- 9.4: the temporary hold. 9.4.1 is its setpoint in 1/200 °C, which is
  `ComfortTemperature` in the cloud API (there in 1/100 °C). 9.4.2 is the end
  time of the hold.
- 9.5: the permanent hold. 9.5.1 is its setpoint in 1/200 °C, which is
  `ManualTemperature` in the cloud API.
- The thermostat keeps both setpoints all the time and uses the one of the
  current mode.
- 9.3 and 9.6 are not interpreted.
- Device info 7.2 is the room name. 7.9 is the setpoint the thermostat is
  aiming for right now, in any mode. It is the only place where the current
  setpoint of the schedule shows up.

## Commands to the thermostat

A command has the shape of a change: field 1 the sequence number, 5 = 1, 8 the
thermostat's own last schedule, and 9 the state it has to take over. That
state is the thermostat's last state with a new time and the new mode. The
setpoint of a hold goes into the field of that hold: 9.5.1 for a permanent
hold, 9.4.1 for a temporary one. Written to the other hold's field, the
thermostat switches mode but keeps the old setpoint.

The gateway changes those fields in the encoded state itself, so every other
byte goes back as the thermostat sent it. It sends one command at a time per
thermostat and waits 10 seconds for the acknowledgement. After that, the state
it sent is the basis for the next command. Its sequence numbers start at 101.

A command is only sent to a thermostat that is connected and has uploaded its
state. It is never kept for later.

The local link cannot start a temporary hold with an end time, or a vacation.

## Relaying

In relay mode the gateway connects to OJ's gateway by IP address, because the
hostname now leads to the gateway itself. It compares the certificate with the
pinned fingerprint before it sends anything, and passes nothing on if they
differ. After that every frame goes on unchanged in both directions, with one
exception: the thermostat's acknowledgement of a command of the gateway is not
passed on, because OJ's gateway never sent that command.

A change made in the vendor's app reaches the thermostat as a command of OJ's
gateway, in the shape described above. The gateway reports it as the new
state.

The two answers that follow the uploads are recorded as the thermostat's
profile: the configuration answer is a message with field 10 and otherwise
only fields 1 and 5, the support answer one with field 18. The gateway repeats
exactly these frames when it answers the thermostat itself. Reported: the
thermostat accepted the two answers of the captured session when another
gateway repeated them.

If OJ's gateway cannot be reached or is not trusted, a thermostat with a
profile is answered locally. The gateway then tries OJ's gateway every five
minutes, and disconnects the thermostat once it works, so the thermostat
connects again and is relayed.

## Time

The gateway sends UTC as the server time, as OJ's gateway does. The thermostat
applies its own time zone.

## Open questions

These need a real thermostat to answer.

- Does OJ's gateway send the configuration and support answers on every new
  connection? Recording the profile depends on it. Reported: it sent both to
  a thermostat that was already set up, on a new connection. That is
  one session, so "every" is still open.
- How do the sequence numbers of OJ's gateway go on? If they reach 101, an
  acknowledgement can be held back for the wrong sender once. The reported
  session had 0 and 1, see [Sequence numbers](#sequence-numbers).
- After a command of the gateway, does the thermostat report its new state to
  OJ's gateway? If not, the vendor's app shows the old state until the next
  upload.
- Does the configuration answer depend on settings a user can change? If it
  does, local mode repeats old settings at every reconnect.
- Do firmware updates use this connection?
- Which gateway host do thermostats of other brands and regions connect to?

## Not decoded yet

- Fault codes.
- Telemetry 15.4 to 15.7, and the device info fields 7.6, 7.7, 7.10, 7.11,
  7.13 and 7.14.
- The schedule (field 8). It is passed back as the thermostat sent it.
  Reported: its temperatures are in 1/100 °C, unlike the setpoints of a hold.
  The thermostat acknowledges a changed schedule without uploading it again,
  and the change shows in the upload after it connects anew.
