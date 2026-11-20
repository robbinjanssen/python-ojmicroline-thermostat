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
on 13 and 14 November 2026 (the reports are in
[pull request 478 of the Home Assistant integration](https://github.com/robbinjanssen/home-assistant-ojmicroline-thermostat/pull/478)).
What that showed is marked as seen. A point marked as reported comes from the
same thermostat, but from its owner's own gateway and captures.

Seen to work: the TLS connection with a self-signed certificate, the relay,
recording the profile, a change through the cloud arriving as a command,
answering the thermostat locally, answering it locally while OJ's gateway was
unreachable, and relaying it again once OJ's gateway was back.

On 13 November the thermostat acknowledged the commands of this gateway 13
to 33 seconds after they were sent, which was too late for the gateway. On 14
November it acknowledged 15 of the same commands within 0.8 to 2.5 seconds,
relayed and answered locally. Why is an [open question](#open-questions).

## Transport

- The thermostat connects to `na-thermos.ojelectronics.com` on TCP port 9533
  with TLS 1.2. It does not check the certificate of the server. Seen: it
  negotiates AES256-SHA256.
- Seen: a thermostat whose login is not answered connects again every 7.6
  seconds. An answer that came 3.6 seconds after it connected was too late,
  one after 0.4 seconds was in time.
- Seen: a heartbeat that is not acknowledged is sent again after 5 seconds
  with the next sequence number. Five seconds after the fourth, the
  thermostat closes the connection and connects again. Seen on 14 November
  when OJ's gateway stopped answering: the gateway then answered the new
  connection locally within 3 seconds.
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
thermostat and waits 40 seconds for the acknowledgement. Its sequence numbers
start at 101, and skip a number that a message of OJ's gateway is still
waiting on.

The thermostat acknowledges in the order it received. The gateway keeps what
it sent the thermostat and what OJ's gateway sent, in that order, and gives
an acknowledgement to the oldest message with its number. Once the thermostat
accepts a command, the state of that command is the basis for the next one and
is reported as the new state. A command of OJ's gateway counts the same way,
once the thermostat accepted it: the thermostat answers in the order in which
it took the states over, so the last one that was accepted is the one it
holds. A command of the gateway that is built while one of OJ's gateway is
still on its way starts from that one, because the thermostat takes it over
first; built on what the thermostat confirmed so far, it would undo a change
made in the vendor's app a moment before. That also holds for an
acknowledgement that
comes after the 40 seconds: the caller got an error by then, but the
thermostat did change. After 60 seconds a command without an answer counts as
lost, and so does one whose 40 seconds are over once OJ's gateway sends a
message with its number. Its number then cannot take the answer to a later
message.

The answers the gateway repeats from a profile keep the numbers OJ's gateway
gave them. The gateway keeps them among what the thermostat has to answer, as
it does for the messages of OJ's gateway.

A command is only sent to a thermostat that is connected and has uploaded its
state. It is never kept for later.

The local link cannot start a temporary hold with an end time, or a vacation.

### Late acknowledgements

Seen on 17 November, on an OWD5 thermostat in local mode: a command was
acknowledged 12.3 seconds after it was sent. At the 10 seconds the gateway
waited then, the TCP connection had one segment that the thermostat had not
acknowledged, sent again 10 times, and the last acknowledgement of the
thermostat was 27 seconds old, with a round trip of 105 ms. So for about
half a minute the thermostat took in nothing at all, not even at the TCP
level, while the network was fine. Two other commands were acknowledged after
5.6 and 0.6 seconds. The router of that tester names the thermostat's Wi-Fi
module HF-LPB100. A Wi-Fi module that saves power by not listening for a
while fits all of this. The gateway therefore waits 40 seconds. Seen on 18
November on the same thermostat: a command sent 8 seconds before a heartbeat
was acknowledged right after that heartbeat, 8.8 seconds after it was sent.

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
state once the thermostat accepted it.

The two answers that follow the uploads are recorded as the thermostat's
profile: the configuration answer is a message with field 10 and otherwise
only fields 1 and 5, the support answer one with field 18. The gateway repeats
exactly these frames when it answers the thermostat itself. Reported: the
thermostat accepted the two answers of the captured session when another
gateway repeated them.

If OJ's gateway cannot be reached or is not trusted, a thermostat with a
profile is answered locally. The same goes for a gateway that takes the
connection and then does not answer the login within 3 seconds, or hangs up:
relaying to it again would keep the thermostat from ever being answered. A
login without an answer only counts against that thermostat: OJ's gateway is
still tried for the others. The gateway then tries OJ's gateway every five
minutes, with the login of the thermostat, and disconnects the thermostat
once that login is answered, so the thermostat connects again and is relayed.
Seen: the thermostat connected again about 60 seconds after the gateway
closed its connection, and could not be reached in between. That is why a
gateway that connects and stays silent is not worth a reconnect.

OJ's gateway gets 3 seconds to accept a connection, and 3 more to answer the
login. For five minutes after it could not be reached, showed a certificate
that does not match the pin, or left the login of a thermostat unanswered, a
thermostat with a profile is answered locally at once, without another
attempt. Seen: with a wait of 10 seconds on every new
connection, a thermostat was never answered while OJ's gateway was
unreachable, because it gives up on its login sooner than that.

### What the cloud makes of a command of the gateway

Reported on 18 November, for a WG4 thermostat with firmware 1012M201 in relay
mode: after the gateway set the manual mode at 65 °F, the reloaded website of
mythermostat.info showed "On Manual Permanently" at 65 °F, and a new session of
the cloud API read mode 3 with that setpoint. The WG4 cloud takes over the
state the thermostat reports, so a command of the gateway shows in the
vendor's website. The WD5 cloud does not, see [State and commands](#state-and-commands).

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
  command and gets its acknowledgement within 10 seconds. On 14 November the
  same commands were all acknowledged within 2.5 seconds. A WD5 thermostat
  showed the cause on 17 November, see
  [Late acknowledgements](#late-acknowledgements); whether a WG4 thermostat
  does the same is not confirmed.
- Does OJ's gateway send the configuration and support answers on every new
  connection? Seen: on each of three connections of a thermostat that was
  already set up.
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
holds the captured frames.

This library ran against two OWD5 thermostats with firmware 1013W213, of an
account of the SWATT app, on 17 November 2026 in relay mode. What that showed
is marked as seen. The times below are local time (UTC+1); the gateway's
history writes UTC.

Seen:

- The thermostat negotiates TLS 1.2 with AES256-SHA256, as the WG4 series
  does. Both thermostats connected within 10 seconds of the gateway starting.
- OJ's gateway answers the login with 3 = 1, 4 = 1 and 12, and then asks for
  the full upload with sequence 0 and fields 4 and 10. The thermostat answers
  with device info, schedule and state (fields 7, 8 and 9).
- The thermostat numbers its messages on from one connection to the next,
  and from 1 again after it was switched off and on. OJ's gateway numbers
  its own commands from 1 on every connection.
- A heartbeat (field 6) comes about once a minute. OJ's gateway acknowledges
  every message with 3 = 1 and the server time in field 12.
- Commands of the gateway were acknowledged within 0.5 seconds, and the
  thermostat reported its new state to OJ's gateway right after.
- After the gateway closed the connections, because Home Assistant reloaded
  it, the thermostats connected again within 33 and 46 seconds.
- One thermostat reported an empty name in 7.2, while the cloud API named
  it. An empty name is therefore not reported.

### Where the thermostat connects

A WD5 thermostat connects to port 9513 of a hostname that holds the customer
ID of its brand:

| Hostname | Brand | How it is known |
| --- | --- | --- |
| `owd5-r1099-thermostat.ojelectronics.com` | Customer 99, the SWATT app | Seen in the DNS requests of an OWD5 thermostat |
| `owd5-sl003-thermostat.ojelectronics.com` | Customer 3, Schlüter in Europe | Reported by the ioBroker adapter |
| `owd5-oj001-thermostat.ojelectronics.com` | Customer 1, the OJ Microline app (OWD5) | Seen in the DNS requests of an OWD5 thermostat |

On 14 and 16 November 2026 these names led to the same address, with one
certificate that is valid until 2044. The hostnames of other brands are not
known.

A brand's cloud API has a hostname of its own, such as
`owd5-sl003-app.ojelectronics.com`. That is the name the apps and this
library's `WD5API` use, not the one the thermostat looks for.

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
of its state that matters in its mode, so that is no base for a command.

The gateway builds the base in three layers. It starts from what the
thermostat reported and what the cloud API knows of it. Over that goes the
last command the thermostat accepted on the connection, of OJ's gateway or of
the gateway itself, or one of OJ's gateway that is still on its way: a
command of OJ's gateway may leave parts out, which then come from the first
layer, and everything it does hold goes back byte for byte. Over that goes
what the thermostat reported since,
so a setpoint that was changed at the wall is not undone by the next command.
That includes the vacation: left out, a command would erase a vacation the
thermostat has stored. A setpoint that nobody knows becomes a default.

The gateway sets the schedule, comfort, manual, boost, eco and frost
protection modes. Reported as working on the OWD5 thermostat: the first five
and the vacation. Frost protection only changes 9.2 and keeps the setpoint in
9.7. Seen: a wall controller switched an OWD5 thermostat to frost protection,
and it reported `mode=6 frost-protection=5.00C`. So did a command of the
gateway, on 17 November, acknowledged within 0.4 seconds. This library
leaves the vacation to the cloud API.

Seen: the end of a comfort hold is the plain local time of the thermostat.
Comfort for 240 minutes, set by the gateway at 22:58:01, ended at 02:58:01.
Comfort for an hour, set in the app at 23:03, came from OJ's gateway with
the end 00:03:00. The thermostat reported both ends as it was sent them.

Seen: the cloud does not take over a mode that the thermostat reports after a
command of the gateway. The gateway set a thermostat from comfort back to the
schedule, and the thermostat said so to OJ's gateway, which acknowledged it.
The app kept showing comfort, also after it was restarted. The cloud keeps
the mode of a group and only changes it itself. That is why `WD5LocalAPI`
sets a mode through the cloud while the gateway relays the thermostat, and
only sends it itself when the cloud does not reach the thermostat.

### Open questions

- The vacation and the boost on a real thermostat. Relay mode, local mode,
  and the schedule, comfort, manual, eco and frost protection modes have
  been seen.
- What 9.8 and 9.9 of the state mean; OJ's gateway sends 1 and 0. And what
  6.5 to 6.7 of the heartbeat mean. Seen: 6.5 was 1 while a comfort hold was
  on, set in the app and set by the gateway, and 0 otherwise.
- Whether the time zone a thermostat reports (7.3) includes summer time.
  Seen: 3600 in November, in Central European time. The end of a comfort
  hold or a boost is written in the local time of the thermostat, so an hour
  of difference would show there in summer. The ioBroker adapter computes
  the end the same way, was used through a summer, and names 3600 and 7200
  as time zones, which suggests that the time zone does include it. A time zone west of UTC has not
  been seen either; it is read as a negative number.
- Which fields of a state OJ's gateway always sends. Its logged commands
  differ: one for the manual mode had no vacation and no boost.
- Which hostnames the thermostats of other brands connect to.
