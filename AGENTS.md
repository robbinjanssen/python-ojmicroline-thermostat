# AGENTS.md

Guidance for AI coding agents working on this repository. Human contributors:
see [CONTRIBUTING](CONTRIBUTING.md).

## Project

`ojmicroline-thermostat`: an asynchronous Python client for OJ Microline cloud
thermostats, published on PyPI. Its main consumer is the
[Home Assistant integration](https://github.com/robbinjanssen/home-assistant-ojmicroline-thermostat)
(same owner), which pins a released version; keep that integration in mind when
changing the public API.

```text
ojmicroline_thermostat/
  ojmicroline.py   OJMicroline client: HTTP requests, session retry, push
                   subscriptions; the OJMicrolineAPI protocol and
                   SessionOJMicrolineAPI base class
  wd5.py           WD5API: WD5 series (OWD5, MWD5), session API
  wg4.py           WG4API: WG4 series (UWG4, AWG4), session API, push long-poll
  local.py         LocalAPI: what the APIs for a local gateway share, with
                   the cloud API for what a thermostat does not report itself
  wg4_local.py     WG4LocalAPI: WG4 series on a local gateway
  wd5_local.py     WD5LocalAPI: WD5 series on a local gateway
  wg5.py           WG5API: WG5 series (UWG5), OAuth2 API
  gateway/         local device gateway, adapted from schluter-local (WG4) and
                   ioBroker.schlueter-thermostat (WD5), both MIT, see
                   gateway/LICENSE
    protobuf.py    wire format: varints, messages, frames
    server.py      DeviceGateway: listener, relay to OJ's gateway, commands;
                   what every series has in common
    wg4.py         the messages of the WG4 series
    wg4_gateway.py WG4Gateway: what a WG4 thermostat says and expects
    profile.py     DeviceProfile: the answers of OJ's gateway to repeat (WG4)
    wd5.py         the messages of the WD5 series
    wd5_gateway.py WD5Gateway: what a WD5 thermostat says and expects
  models/          Thermostat and Schedule dataclasses, parsed per series
  exceptions.py    public exceptions
  const.py         regulation modes and other constants
docs/              device-protocol.md: the protocol of the gateway
tests/             pytest tests with aresponses and JSON fixtures
```

## Commands

The project uses [uv](https://docs.astral.sh/uv/) and
[prek](https://github.com/j178/prek).

```sh
uv sync                        # create .venv with the dev tools
uv run prek install            # git hook running all checks on commit
uv run prek run --all-files    # Ruff, mypy, pylint, pytest, codespell, ...
uv run pytest                  # tests with coverage
uv build --no-sources          # build the wheel and sdist (uv_build)
```

Run all hooks and tests before committing. The hooks refuse commits to `main`;
work on a branch.

## Requirements

- Python 3.11 to 3.14; CI tests every version. Write code that runs on 3.11:
  no PEP 695 generics (`def f[T]`), use `TypeVar`. mypy and Ruff target 3.11.
- Runtime dependencies are only `aiohttp` and `yarl`. Do not import packages
  that are only development dependencies; `async_timeout` once broke clean
  installs that way, use `asyncio.timeout`.

## Conventions

- **Public API**: everything users need is exported from
  `ojmicroline_thermostat/__init__.py` and listed in `__all__`. Constructor
  arguments of the model APIs are public; `WD5API` keeps its positional
  arguments (`noqa: PLR0917`).
- **Model APIs** implement the `OJMicrolineAPI` protocol: `login()`,
  `invalidate_session()`, `get_thermostats()`, `get_energy_usage()`,
  `set_regulation_mode()`, `get_notifications()` and `supports_notifications`.
  Session-based APIs (WD5, WG4) derive from `SessionOJMicrolineAPI`.
- **HTTP**: all requests go through `OJMicroline._request` (the `request`
  attribute of an API), so the session passed to `OJMicroline` is used. It
  supports JSON bodies, form fields (`form=`) and another host (`host=`).
- **Errors**: HTTP 401 raises `OJMicrolineUnauthorizedError`; `OJMicroline`
  then invalidates the session, logs in again and retries once. Other HTTP
  errors raise `OJMicrolineConnectionError`, timeouts `OJMicrolineTimeoutError`,
  and wrong credentials `OJMicrolineAuthError`.
- **Temperatures** are integers in 1/100 °C.
- **Typing**: mypy runs in strict mode from `pyproject.toml`; there is no
  `mypy.ini`.
- **Gateway**: the protocol has no specification; `docs/device-protocol.md`
  lists what is known and what is not, and marks what was seen on a real
  thermostat. The WG4 gateway has run against one, with commands that were
  acknowledged too late; the WD5 gateway has not. Do not present either as
  verified.
  - `DeviceGateway` knows no field of a series besides the sequence number
    and the acknowledgement. What differs per series goes into the hooks of
    its subclass, not into a check on the series.
  - A thermostat answers in the order it received. Match its answer through
    `_Link.unanswered`, never by number alone: OJ's gateway and this one can
    use the same number.
  - A command takes effect when the thermostat accepts it, in the `accept`
    callback of `_send_command()`, also when that is after the caller's wait.
  - A thermostat gives up on its login within seconds. Nothing between its
    login and the answer may wait on the network for long.
  - Change a field with `set_field()`, never by re-encoding a parsed message:
    every byte that is not understood goes back as the thermostat sent it.
  - A login holds the account's email address. Never log or store it;
    `describe()` is the only way a message reaches a log.
  - Report what happens on a connection with `WG4Gateway._event()`, which
    logs it and keeps it in `history` for a report from a real thermostat.
  - Nothing is sent to OJ's gateway before its certificate matched the pin.
  - A cloud failure must not stop or delay a thermostat on the gateway, which
    is why `LocalAPI.login()` does nothing.
  - A WD5 command is a complete state that the thermostat takes over as a
    whole. Build it from the last command or from everything that is known,
    or it erases what the thermostat has stored, such as a vacation.

## Tests

- Tests mock the API with `aresponses` and the JSON responses in
  `tests/fixtures/`. The Home Assistant integration copies these fixtures, so
  keep them realistic.
- Coverage is 100%; CI requires at least 90%. Add a test for every bug fix that
  fails without the fix.
- The gateway tests run a fake thermostat and a fake OJ gateway
  (`tests/gateway_fakes.py`) against a real `WG4Gateway`, over TLS on the
  loopback interface. `trustme` creates the certificates, as the hooks reject
  a committed private key. Timeouts are module constants in `gateway/server.py`
  and `local.py`; shorten them with `monkeypatch` instead of sleeping.
- `config.py` (gitignored) holds real credentials for `test_output.py`, a
  manual script. Never commit it, and never print credentials or tokens.
  `test_output.py`'s import order depends on whether `config.py` exists, which
  is why `I001` is ignored for it in `pyproject.toml`.

## Pull requests and releases

- Every pull request needs one of these labels: `breaking-change`, `bugfix`,
  `hotfix`, `documentation`, `enhancement`, `refactor`, `performance`,
  `new-feature`, `maintenance`, `ci`, `dependencies`, `skip-changelog`.
- Release Drafter derives the next version from the labels. Publishing a GitHub
  release runs `release.yaml`, which sets the version from the tag
  (`uv version`), builds the package and publishes it to PyPI. The version in
  `pyproject.toml` stays `0.0.0`.
- Renovate keeps dependencies up to date and automerges minor and patch
  updates.
