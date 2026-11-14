# Device protocol of WG4-series and WD5-series thermostats

Maintainer notes on how a thermostat talks to OJ's device gateway, as
implemented in [`ojmicroline_thermostat/gateway`](../ojmicroline_thermostat/gateway).
There is no published specification. Most of this document is about the WG4
series; the [WD5 series](#wd5-series) has its own section at the end.

The WG4 field numbers are as observed by
[peskyAdmin/schluter-local](https://github.com/peskyAdmin/schluter-local)
(MIT), whose protocol notes this document is adapted from. The tests in
[`tests/test_gateway_wg4.py`](../tests/test_gateway_wg4.py) pin the behaviour
with frames captured from real thermostats.

This library has run against one real WG4 thermostat, with firmware 1012M201,
on 13 November 2026 (the report is in
[pull request 478 of the Home Assistant integration](https://github.com/robbinjanssen/home-assistant-ojmicroline-thermostat/pull/478)).
What that showed is marked as seen. A point marked as reported comes from the
same thermostat, but from its owner's own gateway and captures.

Seen to work: the TLS connection with a self-signed certificate, the relay,
recording the profile, a change through the cloud arriving as a command, and
answering the thermostat locally. Seen to fail: the thermostat acknowledged
the commands of this gateway 13 to 33 seconds after they were sent, which was
too late for the gateway. Why is an [open question](#open-questions).

## Transport

- The thermostat connects to `na-thermos.ojelectronics.com` on TCP port 9533
  with TLS 1.2. It does not check the certificate of the server. Seen: it
  negotiates AES256-SHA256.
- Seen: a thermostat whose login is not answered connects again every 7.6
  seconds. An answer that came 3.6 seconds after it connected was too late,
  one after 0.4 seconds was in time.
- Seen: a heartbeat that is not acknowledged is sent again after 5 seconds
  with the next sequence number. After four of them the thermostat connects
  again.
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

Seen on three connections: the acknowledgements of OJ's gateway carry the
thermostat's numbers, which were above 5800 and go on from one connection to
the next. OJ's gateway counts its own messages from 0 on every connection:
the support answer is 0 and the configuration answer 1. Reported from the log
of the same test: its commands went on from 2, and it sent a command again,
with the next number, while the thermostat had not acknowledged the one
before.

So on a connection that lasts long enough, the numbers of OJ's gateway reach
those of this gateway, which start at 101. That is why an acknowledgement is
matched by the order in which the messages were sent, see
[Commands to the thermostat](#commands-to-the-thermostat).

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
thermostat and waits 10 seconds for the acknowledgement. Its sequence numbers
start at 101, and skip a number that a message of OJ's gateway is still
waiting on.

The thermostat acknowledges in the order it received. The gateway keeps what
it sent the thermostat and what OJ's gateway sent, in that order, and gives
an acknowledgement to the oldest message with its number. Once the thermostat
accepts a command, the state of that command is the basis for the next one and
is reported as the new state. That also holds for an acknowledgement that
comes after the 10 seconds: the caller got an error by then, but the
thermostat did change. After 60 seconds a command without an answer counts as
lost, and so does one whose 10 seconds are over once OJ's gateway sends a
message with its number. Its number then cannot take the answer to a later
message.

The answers the gateway repeats from a profile keep the numbers OJ's gateway
gave them. The gateway keeps them among what the thermostat has to answer, as
it does for the messages of OJ's gateway.

A command is only sent to a thermostat that is connected and has uploaded its
state. It is never kept for later.

The local link cannot start a temporary hold with an end time, or a vacation.

## Relaying

In relay mode the gateway connects to OJ's gateway by IP address, because the
hostname now leads to the gateway itself. It compares the certificate with the
pinned fingerprint before it sends anything, and passes nothing on if they
differ. After that every frame goes on unchanged in both directions, with one
exception: the thermostat's acknowledgement of a command of the gateway is not
passed on, because OJ's gateway never sent that command. A message the gateway
cannot read goes on as well, so a firmware it does not know keeps its link to
OJ's gateway.

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

OJ's gateway gets 3 seconds to accept a connection. For five minutes after it
could not be reached, or showed a certificate that does not match the pin, a
thermostat with a profile is answered locally at once, without another
attempt. Seen: with a wait of 10 seconds on every new
connection, a thermostat was never answered while OJ's gateway was
unreachable, because it gives up on its login sooner than that.

## Time

The gateway sends UTC as the server time, as OJ's gateway does. The thermostat
applies its own time zone.

## Open questions

These need a real thermostat to answer.

- Why does the thermostat acknowledge a command of this gateway so late?
  Seen: 13, 16.5 and 32 seconds after the command was sent, and once not
  within a minute. In that time nothing else the gateway sent was answered
  either: an acknowledgement of a heartbeat did not arrive, and OJ's gateway
  repeated its commands. Reported: the owner's own gateway sends the same
  command and gets its acknowledgement within 10 seconds. The delays fit the
  moments at which the system sends a TCP segment again, which would mean
  that the command does not reach the thermostat at first. The event of a
  command that timed out therefore tells the state of the TCP connection,
  and that of an acknowledgement tells its value.
- Does OJ's gateway send the configuration and support answers on every new
  connection? Seen: on each of three connections of a thermostat that was
  already set up.
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
  Reported, in [issue 480 of the integration](https://github.com/robbinjanssen/home-assistant-ojmicroline-thermostat/issues/480):
  8.1 is the time of the change and 8.2 the week, which has a day in every
  field 1. A day is {1 its number, 1 for Monday to 7 for Sunday; 2 an event,
  once per event}, and an event {1 the clock as hour, minute and second; 2 the
  temperature; 3 whether it is active}. The thermostat that was looked at has
  six events a day. The temperatures are in 1/100 °C, unlike the setpoints
  of a hold. The thermostat acknowledges a changed schedule without uploading
  it again, and the change shows in the upload after it connects anew.

## WD5 series

The WD5 series (OWD5, MWD5) uses the same frames and the same TLS connection
with other fields. What is known comes from
[patricknitsch/ioBroker.schlueter-thermostat](https://github.com/patricknitsch/ioBroker.schlueter-thermostat)
(MIT), where it was worked out on an OWD5 thermostat with firmware 1013W220.
The module [`gateway/wd5.py`](../ojmicroline_thermostat/gateway/wd5.py) lists
every field, and [`tests/test_gateway_wd5.py`](../tests/test_gateway_wd5.py)
holds the captured frames. This library has not run against a real WD5
thermostat yet.

### Where the thermostat connects

A WD5 thermostat connects to port 9513 of a hostname that holds the customer
ID of its brand:

| Hostname | Brand | How it is known |
| --- | --- | --- |
| `owd5-r1099-thermostat.ojelectronics.com` | Customer 99, the SWATT app | Seen in the DNS requests of an OWD5 thermostat |
| `owd5-sl003-thermostat.ojelectronics.com` | Customer 3, Schlüter in Europe | Reported by the ioBroker adapter |

On 14 November 2026 both names led to the same address, with one certificate
that is valid until 2044. The hostnames of other brands are not known.

### Messages

Field 1 is the sequence number. An acknowledgement repeats it with field 3 set
to 1, and a message that wants an answer has field 4 set to 1.

| From the thermostat | Meaning | Answer of the gateway |
| --- | --- | --- |
| Field 5 | Login: the serial number, the device type, the firmware and the clock. It holds no email address | Acknowledgement with 4 = 1 and 12 = {1: server time}, then a request for the full upload: sequence 0, 4 = 1, 10 = {1: server time, 2: 1} |
| Field 6 | Heartbeat, about once a minute: the room and floor temperature in 1/100 °C, and whether the floor is heating | Acknowledgement with the server time |
| Field 7 | Device info: the name, the time zone in seconds, the load in W | Acknowledgement with the server time |
| Field 8 | The schedule | Acknowledgement with the server time |
| Field 9 | The state, with only the fields that matter in its mode | Acknowledgement with the server time |
| Fields 1 and 3 | The thermostat acknowledges a message it received | Nothing |

Unlike the WG4 series, nothing has to be recorded before the gateway can
answer a thermostat itself: none of these answers depends on the thermostat.

### State and commands

The state is field 9: 9.1 the time of the change, 9.2 the regulation mode in
the codes of the cloud API, 9.3 the comfort hold {1 setpoint, 2 end}, 9.4
manual {1 setpoint}, 9.5 the vacation {1 enabled, 2 first day, 3 last day,
4 setpoint}, 9.6 boost {1 end} and 9.7 frost protection {1 setpoint}.
Setpoints are in 1/200 °C. Days and ends are in the local time of the
thermostat, and 1970-01-01 means that one is not set.

A command is a message with 4 = 1 and a complete state in field 9, which the
thermostat takes over as a whole. The thermostat itself only reports the part
of its state that matters in its mode, so that is no base for a command. The
gateway starts from the last command the thermostat was sent on the
connection, by OJ's gateway or by itself. Before any command it builds the
state from what the thermostat reported and what the cloud API knows of it.
That includes the vacation: left out, a command would erase a vacation the
thermostat has stored. A setpoint that nobody knows becomes a default.

The gateway sets the schedule, comfort, manual, boost and eco modes. Reported
as working on the OWD5 thermostat: those five and the vacation. This library
leaves the vacation and frost protection to the cloud API.

### Open questions

- Everything above, with this library on a real thermostat.
- How fast a thermostat acknowledges a command, given what the WG4 series
  showed.
- What 9.8 and 9.9 of the state mean; OJ's gateway sends 1 and 0. And what
  6.5 to 6.7 of the heartbeat mean.
- Whether the time zone a thermostat reports (7.3) includes summer time. The
  end of a comfort hold or a boost is written in the local time of the
  thermostat, so an hour of difference would show there. A time zone west of
  UTC has not been seen either; it is read as a negative number.
- Which fields of a state OJ's gateway always sends. Its logged commands
  differ: one for the manual mode had no vacation and no boost.
- Which hostnames the thermostats of other brands connect to.
