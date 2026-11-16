# Contributing to GenLauncher-LP

Thanks for helping out! Bug reports, testing on different distros and pull
requests are all welcome.

## Reporting bugs

1. Check the [open issues](https://github.com/NeleBiH/GenLauncher-LP/issues)
   first — it may already be reported.
2. Open a new issue with the **Bug report** template.
3. Attach the launcher log. In the launcher, open **About → Copy Log Path**;
   the log is `~/.local/state/genlauncher/genlauncher.log`.

Problems with a specific mod's download link usually come from the shared
GenLauncher mod catalog, not from this launcher — mention the mod name and the
error message so we can tell the two apart.

## Development setup

```bash
git clone https://github.com/NeleBiH/GenLauncher-LP.git
cd GenLauncher-LP
./run.sh          # creates .venv, installs dependencies, starts the GUI
```

Run the tests before opening a pull request:

```bash
.venv/bin/python -m pytest
```

## Pull requests

- Keep each pull request focused on one change.
- Match the style of the surrounding code (PySide6 widgets, type hints,
  `from __future__ import annotations`).
- Add or update tests in `tests/` for changes in `src/genlauncher/core/`.
- Describe how you tested the change — distro, Proton version, game
  (Generals or Zero Hour) and mod, if relevant.

## License

GenLauncher-LP is GPL-3.0-or-later. By contributing you agree that your
contribution is released under the same license.
