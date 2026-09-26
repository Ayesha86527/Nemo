// Stage the renderer for packaging: copy index.html/styles.css/fonts next to
// the esbuild bundle in dist/renderer and rewrite the dev script path so the
// packaged app loads one self-contained directory.
import { copyFileSync, mkdirSync, readFileSync, writeFileSync, cpSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src", "renderer");
const out = join(root, "dist", "renderer");

mkdirSync(out, { recursive: true });
cpSync(join(src, "fonts"), join(out, "fonts"), { recursive: true });
copyFileSync(join(src, "styles.css"), join(out, "styles.css"));

const html = readFileSync(join(src, "index.html"), "utf8");
const staged = html.replace("../../dist/renderer/bundle.js", "./bundle.js");
writeFileSync(join(out, "index.html"), staged);
console.log("Staged renderer ->", out);
