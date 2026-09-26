# Packaging Nemo

How to build distributable installers (Windows NSIS, macOS DMG, Linux AppImage)
with the Python backend bundled, so end users need no Python or Node.

## Overview

A packaged Nemo build contains:

- The Electron shell (`dist/main.js`, `dist/preload.js`).
- A staged, self-contained renderer (`dist/renderer/`: bundle + `index.html` +
  `styles.css` + `fonts/`). All npm libraries are bundled by esbuild, so no
  `node_modules` ship with the app.
- The backend as a **PyInstaller one-file executable** under `resources/backend/`
  (`nemo-backend.exe` on Windows, `nemo-backend` elsewhere), placed by
  electron-builder into the app's resources.

At startup the app launches the bundled executable if present and otherwise
falls back to `python -m app.main` from the bundled backend source — so dev
machines keep working unchanged.

## Build steps

### 1. Build the Python sidecar

From `backend/` (Python 3.12+):

```bash
pip install -e . pyinstaller

pyinstaller --onefile --name nemo-backend \
  --collect-all uvicorn \
  --collect-all sqlmodel \
  --collect-all app \
  --distpath ../frontend/resources/backend \
  --workpath build/pyi \
  --specpath build/pyi \
  app/main.py
```

This produces `frontend/resources/backend/nemo-backend(.exe)`.

### 2. Build the installer

From `frontend/`:

```bash
npm install
npm run dist          # current platform
npm run dist:win      # Windows NSIS installer
npm run dist:mac      # macOS DMG (build on macOS; notarize for distribution)
npm run dist:linux    # Linux AppImage
```

Output lands in `frontend/release/`.

## Notes & current limitations

- **Icons**: drop `build/icon.ico` / `icon.icns` / `icons/` (256px+ PNG) into
  `frontend/build/` to replace the default Electron icon.
- **First launch**: the packaged app writes to `~/.nemo/` exactly like the dev
  build — no migration needed.
- **Auto-update** is not configured yet. electron-builder supports publishing
  updates via `publish` config (e.g. GitHub Releases) when you're ready.
- **Cross-compiling** the PyInstaller sidecar is not supported — build the
  sidecar on each target OS (or CI matrix) before running `npm run dist:…`.
- **macOS notarization** requires Apple Developer credentials; configure
  `mac.identity` / `mac.notarize` in `electron-builder.yml` when you have them.
