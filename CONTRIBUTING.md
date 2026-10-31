# Contributing to iranquant

Thanks for helping improve `iranquant`. This guide covers local setup and the
checks a pull request must pass.

## Development setup

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,docs]"
```

This installs the runtime dependencies plus the test, lint, type-check and
documentation toolchains.

## Quality gates

Run the same checks as CI before opening a pull request:

```bash
ruff check src tests examples
ruff format --check src tests examples
mypy src tests
pytest -m "not live"
mkdocs build --strict
```

Live integration tests hit TSETMC and Codal and are opt-in:

```bash
IRANQUANT_LIVE=1 pytest -m live
```

`ruff format` will rewrite files in place; run it without `--check` when the
format check fails.

## Guidelines

- Keep the public API in `iranquant/__init__.py::__all__` in sync with exports.
- Every data function must support `engine="pandas"` (default) and
  `engine="polars"`, returning the corresponding frame type.
- Add or update tests for behavior changes; network tests belong behind the
  `live` marker.
- Update the docstrings and the relevant pages under `docs/` when public
  behavior changes.
- Do not commit credentials, local proxy settings or generated artifacts
  (`dist/`, `site/`, caches).
- Keep commits focused and describe the motivation in the message.

## Reporting issues

Include the Python version, operating system, the exact call or CLI command,
and the full traceback. For upstream payload problems, include a redacted
sample of the response when possible.
