import { app, BrowserWindow, session } from "electron";
import { spawn, ChildProcess } from "child_process";
import * as path from "path";

let backendProcess: ChildProcess | null = null;
let backendPort: number | null = null;
let mainWindow: BrowserWindow | null = null;

const BACKEND_TIMEOUT_MS = 15000;

function startBackend(): Promise<number> {
  return new Promise((resolve, reject) => {
    const pythonCmd = process.platform === "win32" ? "python" : "python3";
    const backendDir = path.join(__dirname, "..", "..", "backend");

    backendProcess = spawn(pythonCmd, ["-m", "app.main"], {
      cwd: backendDir,
      stdio: ["ignore", "pipe", "pipe"],
    });

    backendProcess.stdout?.on("data", (data: Buffer) => {
      const output = data.toString().trim();
      const portMatch = output.match(/NEMO_PORT:(\d+)/);
      if (portMatch) {
        backendPort = parseInt(portMatch[1], 10);
        resolve(backendPort);
        return;
      }
      const dbError = output.match(/NEMO_DB_ERROR:(.*)/);
      if (dbError) {
        reject(new Error(`Database initialization failed:${dbError[1]}`));
      }
    });

    backendProcess.stderr?.on("data", (data: Buffer) => {
      console.error(`[Backend] ${data.toString().trim()}`);
    });

    backendProcess.on("error", (err) => {
      reject(new Error(`Failed to start the Python backend: ${err.message}`));
    });

    backendProcess.on("exit", (code) => {
      if (backendPort === null) {
        reject(new Error(`Backend exited with code ${code} before reporting a port`));
      }
    });

    setTimeout(() => {
      if (backendPort === null) {
        backendProcess?.kill();
        reject(new Error("Backend startup timed out (15s)"));
      }
    }, BACKEND_TIMEOUT_MS);
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
  // the port is delivered over IPC once the sidecar is ready.
  const indexPath = path.join(__dirname, "..", "src", "renderer", "index.html");
  await win.loadFile(indexPath);

  startBackend()
    .then((port) => {
      console.log(`[Nemo] Backend running on port ${port}`);
      if (!win.isDestroyed()) win.webContents.send("nemo:backend-port", port);
    })
    .catch((err) => {
      console.error("[Nemo] Backend failed to start:", err);
      if (!win.isDestroyed()) win.webContents.send("nemo:backend-error", String(err?.message || err));
    });
}

app.whenReady().then(async () => {
  // Local-first app: grant the renderer its own permission requests (mic for voice input,
  // clipboard for copy buttons) instead of showing Electron's default denial.
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => {
    callback(true);
  });
  await createWindow();
});

app.on("window-all-closed", () => {
  backendProcess?.kill();
  app.quit();
});

app.on("before-quit", () => {
  backendProcess?.kill();
});
