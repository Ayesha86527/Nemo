import { app, BrowserWindow, session } from "electron";
import { spawn, ChildProcess, execFile } from "child_process";
import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";

let backendProcess: ChildProcess | null = null;
let backendPort: number | null = null;
let mainWindow: BrowserWindow | null = null;
let restartAttempts = 0;
let quitting = false;

const BACKEND_TIMEOUT_MS = 15000;
const MAX_RESTART_ATTEMPTS = 3;

// Per-launch shared secret: the renderer must present it on every /api call,
// so other local processes or webpages probing 127.0.0.1 can't touch app data.
const authToken = crypto.randomBytes(32).toString("hex");

function notifyRenderer(channel: string, payload: string) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function killBackend() {
  const proc = backendProcess;
  backendProcess = null;
  if (!proc) return;
  if (process.platform === "win32" && proc.pid) {
    // kill() doesn't terminate a process tree on Windows; taskkill does.
    execFile("taskkill", ["/pid", String(proc.pid), "/T", "/F"], () => {});
  } else {
    proc.kill();
  }
}

// In a packaged build: run the PyInstaller sidecar bundled under resources/ if
// present, else fall back to the shipped backend source with a local Python.
// In dev: run the backend source directly from the repo.
function backendLaunchPlan(): { cmd: string; args: string[]; cwd: string } {
  const pythonCmd = process.platform === "win32" ? "python" : "python3";
  if (app.isPackaged) {
    const resources = process.resourcesPath;
    const exe = path.join(resources, "backend", `nemo-backend${process.platform === "win32" ? ".exe" : ""}`);
    if (fs.existsSync(exe)) {
      return { cmd: exe, args: [], cwd: path.dirname(exe) };
    }
    return { cmd: pythonCmd, args: ["-m", "app.main"], cwd: path.join(resources, "backend-src") };
  }
  return { cmd: pythonCmd, args: ["-m", "app.main"], cwd: path.join(__dirname, "..", "..", "backend") };
}

function startBackend(): Promise<number> {
  return new Promise((resolve, reject) => {
    const { cmd, args, cwd: backendDir } = backendLaunchPlan();

    backendProcess = spawn(cmd, args, {
      cwd: backendDir,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, NEMO_AUTH_TOKEN: authToken, PYTHONUNBUFFERED: "1" },
    });

    let settled = false;

    backendProcess.stdout?.on("data", (data: Buffer) => {
      const output = data.toString().trim();
      const portMatch = output.match(/NEMO_PORT:(\d+)/);
      if (portMatch) {
        backendPort = parseInt(portMatch[1], 10);
        settled = true;
        resolve(backendPort);
        return;
      }
      const dbError = output.match(/NEMO_DB_ERROR:(.*)/);
      if (dbError) {
        settled = true;
        reject(new Error(`Database initialization failed:${dbError[1]}`));
      }
    });

    backendProcess.stderr?.on("data", (data: Buffer) => {
      console.error(`[Backend] ${data.toString().trim()}`);
    });

    backendProcess.on("error", (err) => {
      if (!settled) {
        settled = true;
        reject(new Error(`Failed to start the Python backend: ${err.message}`));
      }
    });

    backendProcess.on("exit", (code) => {
      if (!settled) {
        settled = true;
        reject(new Error(`Backend exited with code ${code} before reporting a port`));
      } else if (backendPort !== null && !quitting) {
        // The sidecar died mid-session — surface it and try to come back.
        backendPort = null;
        restartBackend(`The backend stopped unexpectedly (exit code ${code}).`);
      }
    });

    setTimeout(() => {
      if (!settled) {
        settled = true;
        killBackend();
        reject(new Error("Backend startup timed out (15s)"));
      }
    }, BACKEND_TIMEOUT_MS);
  });
}

function restartBackend(reason: string) {
  if (quitting) return;
  if (restartAttempts >= MAX_RESTART_ATTEMPTS) {
    notifyRenderer(
      "nemo:backend-error",
      `${reason} Restarted ${MAX_RESTART_ATTEMPTS} times without success — please restart the app.`
    );
    return;
  }
  restartAttempts += 1;
  console.warn(`[Nemo] ${reason} Restarting (attempt ${restartAttempts}/${MAX_RESTART_ATTEMPTS})…`);
  startBackend()
    .then((port) => {
      restartAttempts = 0;
      console.log(`[Nemo] Backend restarted on port ${port}`);
      notifyRenderer("nemo:backend-port", String(port));
    })
    .catch((err) => {
      restartBackend(String(err?.message || err));
    });
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow = win;

  // Load immediately so the welcome screen appears while the backend boots —
  // the port is delivered over IPC once the sidecar is ready. Packaged builds
  // load the staged renderer (dist/renderer); dev loads from src/.
  const indexPath = app.isPackaged
    ? path.join(__dirname, "..", "renderer", "index.html")
    : path.join(__dirname, "..", "src", "renderer", "index.html");
  await win.loadFile(indexPath);

  // Deliver the auth token before the port lands so the very first /api
  // request already carries it.
  if (!win.isDestroyed()) win.webContents.send("nemo:auth-token", authToken);

  startBackend()
    .then((port) => {
      console.log(`[Nemo] Backend running on port ${port}`);
      if (!win.isDestroyed()) win.webContents.send("nemo:backend-port", port);
    })
    .catch((err) => {
      console.error("[Nemo] Backend failed to start:", err);
      restartBackend(String(err?.message || err));
    });
}

app.whenReady().then(async () => {
  // Local-first app: grant only the permissions the UI actually uses (mic for
  // voice input, clipboard for copy buttons) instead of blanket-approving
  // every request the renderer could ever make.
  const allowedPermissions = new Set(["media", "clipboard-read", "clipboard-sanitized-write"]);
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(allowedPermissions.has(permission));
  });
  await createWindow();
});

app.on("window-all-closed", () => {
  quitting = true;
  killBackend();
  app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  killBackend();
});
