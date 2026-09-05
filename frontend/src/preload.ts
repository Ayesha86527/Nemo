import { contextBridge, ipcRenderer } from "electron";

// The backend port may arrive two ways: a query parameter (dev/manual
// launch) or IPC once the sidecar finishes booting. Until then the URL is
// empty so the renderer shows a branded "starting" state instead of errors.
const urlParams = new URLSearchParams(window.location.search);
let backendPort = urlParams.get("port") || "";
let backendError = "";

ipcRenderer.on("nemo:backend-port", (_event, port: number) => {
  backendPort = String(port);
});

ipcRenderer.on("nemo:backend-error", (_event, message: string) => {
  backendError = message;
});

contextBridge.exposeInMainWorld("nemoAPI", {
  getBackendUrl: () => (backendPort ? `http://127.0.0.1:${backendPort}` : ""),
  getBackendError: () => backendError,
});
