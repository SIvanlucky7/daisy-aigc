import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const dist = path.join(root, "dist");

async function loadDotEnv() {
  const envPath = path.join(root, ".env");
  if (!existsSync(envPath)) return;
  const text = await readFile(envPath, "utf8");
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    const [, key, rawValue] = match;
    if (process.env[key] !== undefined) continue;
    process.env[key] = rawValue.replace(/^["']|["']$/g, "");
  }
}

const files = [
  "index.html",
  "admin.html",
  "about.html",
  "contact.html",
  "privacy.html",
  "terms.html",
  "refund.html",
  "disclaimer.html",
  "styles.css",
  "app.js",
  "admin.js",
  "icons.js",
  "favicon.svg",
  "supabase-auth.js",
  "auth-pages.js",
];

const dirs = ["login", "register", "forgot-password", "reset-password", "dashboard", "pay", "vendor", "assets"];

async function copyIfExists(source, target) {
  if (!existsSync(source)) return;
  await cp(source, target, { recursive: true });
}

await loadDotEnv();

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });

await mkdir(path.join(root, "vendor"), { recursive: true });
const supabaseUmd = await readFile(
  path.join(root, "node_modules", "@supabase", "supabase-js", "dist", "umd", "supabase.js"),
  "utf8",
);
await writeFile(
  path.join(root, "vendor", "supabase.js"),
  `${supabaseUmd}\n;window.supabase = window.supabase || supabase;\n`,
  "utf8",
);

for (const file of files) {
  await copyIfExists(path.join(root, file), path.join(dist, file));
}

for (const dir of dirs) {
  await copyIfExists(path.join(root, dir), path.join(dist, dir));
}

const publicConfig = {
  VITE_SUPABASE_URL: process.env.VITE_SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || "",
  VITE_SUPABASE_ANON_KEY: process.env.VITE_SUPABASE_ANON_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "",
  API_BASE_URL:
    process.env.API_BASE_URL ||
    process.env.VITE_API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    "",
};

await writeFile(path.join(dist, "config.json"), JSON.stringify(publicConfig, null, 2), "utf8");
await writeFile(
  path.join(dist, "_redirects"),
  [
    "/login /login/index.html 200",
    "/register /register/index.html 200",
    "/forgot-password /forgot-password/index.html 200",
    "/reset-password /reset-password/index.html 200",
    "/dashboard /dashboard/index.html 200",
    "/* /index.html 200",
    "",
  ].join("\n"),
  "utf8",
);

console.log("Static site built to dist/");
