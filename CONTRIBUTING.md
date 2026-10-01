# Contributing to ELIES

Thank you for helping! Bug reports, fixes, documentation and new analysis
modules are all welcome.

## Reporting issues

- Bugs and feature requests: open an issue with what you did, what you
  expected and what happened (logs help; every API response has an
  `X-Request-ID` you can search the logs for).
- Frontend issues go to
  [elies-frontend](https://github.com/researchintegrity/elies-frontend/issues).
- Security problems: **do not** open an issue, see [SECURITY.md](SECURITY.md).

## Development

Set up the stack and a virtual environment as described in
[docs/development/setup.md](docs/development/setup.md):

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

Before opening a pull request:

```bash
ruff check .
pytest -m "not integration and not e2e"
python scripts/generate_api_reference.py   # if you changed routes or their docstrings
```

CI runs the same checks on every pull request.

## Guidelines

- Branch from `dev` and open pull requests against `dev`.
- Keep changes focused; explain the why in the pull request description and
  reference the issue (`Fixes #123`).
- Add tests for bug fixes and new behaviour. Unit tests go in `tests/unit/`
  and must not need running services (see
  [docs/development/testing.md](docs/development/testing.md)).
- Follow the existing code style: ruff-clean, type hints on new functions,
  log with `%s` arguments (not f-strings), domain errors from `app/exceptions.py`
  instead of `HTTPException` in services.
- Timestamps are timezone-aware UTC (`datetime.now(timezone.utc)`).
- New settings are read in `app/config/settings.py` and documented in
  [docs/development/configuration.md](docs/development/configuration.md).

## License

By contributing you agree that your contributions are licensed under the
project's [AGPLv3 license](LICENSE).
