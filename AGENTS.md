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
  wg5.py           WG5API: WG5 series (UWG5), OAuth2 API
  models/          Thermostat and Schedule dataclasses, parsed per series
  exceptions.py    public exceptions
  const.py         regulation modes and other constants
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

## Tests

- Tests mock the API with `aresponses` and the JSON responses in
  `tests/fixtures/`. The Home Assistant integration copies these fixtures, so
  keep them realistic.
- Coverage is 100%; CI requires at least 90%. Add a test for every bug fix that
  fails without the fix.
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
