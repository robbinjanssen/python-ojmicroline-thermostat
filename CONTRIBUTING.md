# Contributing

When contributing to this repository, please first discuss the change you wish
to make via issue, email, or any other method with the owners of this repository
before making a change.

Please note we have a code of conduct, please follow it in all your interactions
with the project.

## Issues and feature requests

You've found a bug in the source code, a mistake in the documentation or maybe
you'd like a new feature? You can help us by submitting an issue to our
[GitHub Repository][github]. Before you create an issue, make sure you search
the archive, maybe your question was already answered.

Even better: You could submit a pull request with a fix / new feature!

## Development environment

This Python project is fully managed using the [uv](https://docs.astral.sh/uv/) package manager.

You need at least:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Install all packages, including all development requirements:

```bash
uv sync
```

uv creates a virtual environment in `.venv` and installs all necessary
packages in it. Run commands in it with `uv run`.

Set up the pre-commit checks with [prek](https://prek.j178.dev):

```bash
uv run prek install
```

*Now you're all set to get started!*

All changes are linted and tested with each commit. You can run all checks
and tests manually, using the following command:

```bash
uv run prek run --all-files
```

To run just the Python tests:

```bash
uv run pytest
```

## Pull request process

1. Search our repository for open or closed [pull requests][prs] that relates
   to your submission. You don't want to duplicate effort.

1. You may merge the pull request in once you have the sign-off of two other
   developers, or if you do not have permission to do that, you may request
   the second reviewer to merge it for you.

[github]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/issues
[prs]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/pulls
