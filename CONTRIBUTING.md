# Contributing to AutoBloodWeb

Thanks for your interest! Bug reports, translations and fixes are all welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and ideas

- Use the [issue forms](https://github.com/Azazel-no/AutoBloodWeb/issues/new/choose). In the app, **Settings → Report a bug…** opens the form and the folder with `app.log`.
- Check logs and screenshots for personal information (Windows user name, nicknames) before attaching them.
- Security problems go through private reporting, see [SECURITY.md](SECURITY.md).

## Ground rules

- AutoBloodWeb only reads screen pixels and makes normal mouse clicks. Changes that read or write game memory or files, inject into the game, or touch the anti-cheat will not be accepted.
- **F9 must always stop automation and release the mouse.**
- Keep changes focused. Fix the module that has the problem instead of rewriting it.
- New user-facing text needs all five languages: English and Russian in `src/core/ui_strings.py` (or the relevant `*_strings.py`), Spanish, German and French in `src/core/locale_extra.py`. If you cannot translate, add English and say so in the PR.
- Do not commit personal data, full-screen game captures with nicknames, or third-party assets without a compatible license. List new dependencies and data in `THIRD_PARTY_NOTICES.md`.

## Development setup

Requirements: Windows 10/11, Python 3.15, Dead by Daylight for live checks.

```powershell
git clone https://github.com/Azazel-no/AutoBloodWeb.git
cd AutoBloodWeb
py -3.15 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m src.app --auto-elevate
```

The app must run as administrator (`--auto-elevate` asks for it): the game runs elevated, and Windows blocks input from a non-elevated process.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

A few tests open real windows and can fail if another window takes focus while they run; rerun them on their own.

Tests and simulations do not prove the app works in the game. If your change affects detection or clicking, also test it in the game through the app's normal **Start** and describe what you checked (role, UI/HUD scale, result) in the PR.

## Building

See "Building from source" in the [README](README.md#building-from-source).

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Make your change with tests where it makes sense.
3. Run `python -m pytest -q`.
4. Open a PR and fill in the template.

By submitting a contribution you agree that it is licensed under the [MIT License](LICENSE).
