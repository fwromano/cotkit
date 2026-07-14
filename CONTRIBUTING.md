# Contributing to cotkit

This library rides in front of real emergency-response operations.
The bar is: **boring, obvious, tested.**

## Ground rules

1. **Stdlib only** in `cotkit/`. A change that adds a dependency needs a
   very good reason and an optional extra, not a hard requirement.
2. **All XML via ElementTree.** String-interpolated XML is an automatic
   rejection — it's the defect class this library exists to kill.
3. **Every behavior gets a test** against `cotkit.testing.FakeTakServer`
   (network) or plain unit tests (parsing/building). Timing-sensitive
   tests use `wait_for`, never bare sleeps.
4. **Policies stay visible.** New timeouts/limits are named constants or
   dataclass fields with docstrings saying *why* that value.
5. **Compatibility floor: Python 3.9.** No match statements, no
   `X | Y` unions outside `from __future__ import annotations` scope.

## Workflow

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

PRs need: green tests and a CHANGELOG entry under "Unreleased".

## Maintenance & releases

- Report problems via GitHub issues with the failing input (an event
  XML, a GeoJSON feature, a pcap excerpt) — most fixes start as a new
  regression test in `tests/`.
- Versioning is semantic: patch = fixes, minor = new tools, major =
  breaking API. Releases are tagged; consumers who vendor should record
  the commit they copied.

## Scope note

cotkit was distilled from ~10 field bridge implementations; several
"missing" features (servers, Marti APIs, persistence, dashboards) are
deliberate scope exclusions, not oversights — see the Scope section of
the README before proposing additions.
