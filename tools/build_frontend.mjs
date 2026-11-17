// Dependency-free Vercel build. Never copy the repository root or any secrets.
import { mkdir, readFile, writeFile, copyFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";

const origin = new URL(process.env.BACKEND_ORIGIN || "");
if (origin.protocol !== "https:" || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) {
    throw new Error("BACKEND_ORIGIN must be an HTTPS origin without credentials/path/query");
}
const output = resolve("dist");
await mkdir(output, { recursive: true });
const files = ["index.html", "style.css", "auth-client.js", "script.js", "data-manager.js"];
if ((await readdir(output)).some(file => !files.includes(file))) {
    throw new Error("Unexpected output files: review dist manually before deployment");
}
for (const file of files) {
    await copyFile(resolve(file), resolve(output, file));
}
const config = {
    framework: null,
    buildCommand: "node tools/build_frontend.mjs",
    outputDirectory: "dist",
    rewrites: [
        { source: "/api/:path*", destination: `${origin.origin}/api/:path*` },
        { source: "/app", destination: "/index.html" }
    ],
    headers: [
        { source: "/api/:path*", headers: [{ key: "Cache-Control", value: "no-store" }] },
        { source: "/:path*", headers: [
            { key: "X-Content-Type-Options", value: "nosniff" },
            { key: "Referrer-Policy", value: "same-origin" },
            { key: "X-Frame-Options", value: "DENY" },
            { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" }
        ] }
    ]
};
// Vercel routing config must exist before its build starts; prepare this file
// locally, commit it after approval, then deploy. The build checks it is current.
const filename = resolve("vercel.json");
if (process.argv.includes("--configure")) {
    await writeFile(filename, JSON.stringify(config, null, 2) + "\n");
    console.log("Prepared public Vercel routing configuration (no secrets).");
} else {
    const existing = JSON.parse(await readFile(filename, "utf8"));
    if (existing.rewrites?.[0]?.destination !== config.rewrites[0].destination) {
        throw new Error("Prepare vercel.json with --configure for the selected BACKEND_ORIGIN before deploying");
    }
    for (const expected of config.rewrites) {
        if (!existing.rewrites.some(rule => rule.source === expected.source && rule.destination === expected.destination)) {
            throw new Error("Required API/app rewrite is missing or incorrect; prepare vercel.json with --configure");
        }
    }
    for (const rule of existing.headers || []) {
        if (!Array.isArray(rule.headers) || rule.headers.some(header =>
            typeof header.key !== "string" || !header.key.trim() ||
            typeof header.value !== "string" || !header.value.trim())) {
            throw new Error("Security header keys and values must not be empty");
        }
    }
    for (const expected of config.headers) {
        for (const header of expected.headers) {
            if (!(existing.headers || []).some(rule => rule.source === expected.source &&
                rule.headers.some(item => item.key.toLowerCase() === header.key.toLowerCase() && item.value === header.value))) {
                throw new Error("Required security/cache header is missing or incorrect; prepare vercel.json with --configure");
            }
        }
    }
}
console.log("Frontend output prepared from the five-file allowlist.");
