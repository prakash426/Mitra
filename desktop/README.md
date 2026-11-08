# Mitra desktop shell (milestone seven)

A thin Tauri window around the local Mitra API. It owns the window only:
no reasoning, no tools, no secrets. Everything still runs through
`http://127.0.0.1:8931`.

Status: compiles clean (`cargo check` and `cargo build` green) and the
binary boots into its event loop without crashing. Single command
launch plus an unsigned Mitra.app are ready. Signing and notarization
are still to come.

## Run it, one command

```sh
bash scripts/macos/mitra-launch.sh
```

That starts the API when it is down, waits for health, then opens the
desktop binary when it is built, else the HUD in your browser.

```sh
# optional: build the desktop binary first
cd desktop/src-tauri
cargo build
```

For live reload during UI work, install the Tauri CLI once
(`cargo install tauri-cli`) and use `cargo tauri dev` from `desktop/`.
The dev hook boots the API first through the same launcher.
`cargo tauri build` produces a distributable app bundle; signing and
notarization are still to come.

## Mitra.app for Login Items

```sh
bash scripts/macos/build-app.sh
```

Drag `dist/Mitra.app` to `/Applications`, then add it under
System Settings, General, Login Items. One click starts the API and
opens Mitra, which greets you with a recap of your last session plus
open tasks and approvals. First launch needs a right click, Open
approval because the bundle is unsigned.

## Files

* `src-tauri/tauri.conf.json`: window points at the local API and HUD.
  The dev hook boots the API through the launcher script.
* `src-tauri/`: minimal Rust shell (main window, default capability).
* `src-tauri/icon.png`: 1024x1024 reactor mark, embedded at compile time.
* `src-tauri/Cargo.lock`: pinned for reproducible builds.
