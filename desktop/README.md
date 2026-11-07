# Mitra desktop shell (scaffold, milestone five)

A thin Tauri window around the local Mitra API. It owns the window only:
no reasoning, no tools, no secrets. Everything still runs through
`http://127.0.0.1:8931`.

Status: scaffold. It has not been compiled here because this machine has
no Rust toolchain in the build sandbox. The files follow the standard
Tauri v2 layout and are ready to build on your Mac.

## Build on your Mac

```sh
# one time: install Rust plus the Tauri CLI
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install tauri-cli

# start the Mitra API first (separate terminal)
python -m mitra.api

# then run the desktop shell
cd desktop
cargo tauri dev
```

`cargo tauri build` produces a distributable app bundle when you want one.

## Files

* `tauri.conf.json`: window points at the local API and HUD.
* `src-tauri/`: minimal Rust shell (main window, default capability).
* Icon: add `icon.png` (1024x1024) next to the config before building
  a release bundle; dev runs fine without it.
