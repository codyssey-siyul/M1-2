// Real auth-client script, fake fetch/DOM. No cookies or keys from a real account.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup(initialHandler) {
    const nodes = new Map();
    const events = [];
    const calls = [];
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, { hidden: false, disabled: false, value: "", textContent: "", listeners: {}, addEventListener(name, handler) { this.listeners[name] = handler; } });
        return nodes.get(id);
    }
    let handler = initialHandler || (async () => response({}, 401));
    const context = vm.createContext({
        document: { getElementById: node, querySelector: node, dispatchEvent(event) { events.push(event.type); } },
        Event: class { constructor(type) { this.type = type; } },
        fetch: async (url, options) => { calls.push({ url, options }); return handler(url, options); },
        localStorage: new Proxy({}, { get() { throw new Error("No localStorage allowed"); } }),
        sessionStorage: new Proxy({}, { get() { throw new Error("No sessionStorage allowed"); } }),
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "auth-client.js"), "utf8"), context);
    return { node, calls, events, auth: context.Auth, setHandler(fn) { handler = fn; } };
}
function response(data, status = 200) {
    return { ok: status < 400, status, json: async () => data, headers: { get: () => "10" } };
}
const user = { username: "test-evaluator", csrf_token: "test-only-csrf", expires_at: 1800000000 };

async function login(h) {
    h.node("login-username").value = "test-evaluator";
    h.node("login-password").value = "test-only-password";
    await h.node("login-form").listeners.submit({ preventDefault() {} });
}

async function run() {
    const h = setup();
    assert.equal(await h.auth.ready, false);
    assert.equal(h.node(".app-layout").hidden, true);
    h.setHandler(async url => url === "/api/auth/login" ? response(user) : response({ ok: true }));
    await login(h);
    assert.equal(h.node(".app-layout").hidden, false);
    assert.equal(h.node("login-page").hidden, true);
    assert.equal(h.node("login-password").value, "");
    await h.auth.fetch("/api/data", { method: "POST", body: "{}" });
    const request = h.calls.at(-1).options;
    assert.equal(request.credentials, "same-origin");
    assert.equal(request.headers["X-CSRF-Token"], user.csrf_token);
    assert.ok(h.events.includes("m1-authenticated"));
    // Failures must not pretend the HttpOnly cookie was invalidated.
    h.setHandler(async () => { throw new Error("offline"); });
    await h.node("logout-button").listeners.click();
    assert.equal(h.node(".app-layout").hidden, false);
    assert.match(h.node("account-name").textContent, /로그아웃 실패/);
    h.setHandler(async () => response({ message: "ok" }));
    await h.node("logout-button").listeners.click();
    assert.equal(h.node(".app-layout").hidden, true);
    assert.equal(h.node("account-name").textContent, "");
    await assert.rejects(() => h.auth.fetch("/api/data"), /로그인/);

    const restored = setup(async () => response(user));
    assert.equal(await restored.auth.ready, true);
    restored.setHandler(async () => response({}, 401));
    await assert.rejects(() => restored.auth.fetch("/api/data"), /세션이 만료/);
    assert.equal(restored.node(".app-layout").hidden, true);

    // A stale initial /me failure cannot undo a later successful login.
    let releaseMe;
    const race = setup(() => new Promise(resolve => { releaseMe = resolve; }));
    race.setHandler(async () => response(user));
    await login(race);
    releaseMe(response({}, 401));
    await race.auth.ready;
    assert.equal(race.node(".app-layout").hidden, false);

    const throttled = setup();
    await throttled.auth.ready;
    throttled.setHandler(async () => response({ detail: "사용 한도" }, 429));
    await login(throttled);
    assert.match(throttled.node("login-status").textContent, /10초/);
    assert.equal(throttled.node("login-password").value, "");

    // Protect response bodies arriving after logout, not just response headers.
    const stale = setup(async () => response(user));
    await stale.auth.ready;
    let body;
    stale.setHandler(async url => url === "/api/data" ? { ok: true, status: 200, json: () => new Promise(resolve => { body = resolve; }) } : response({}));
    const pendingResponse = await stale.auth.fetch("/api/data");
    const pendingBody = pendingResponse.json();
    await stale.node("logout-button").listeners.click();
    body({ data: ["private-test-data"] });
    await assert.rejects(() => pendingBody, /로그인 상태가 변경/);
    console.log("Auth UI: login/logout, CSRF, HttpOnly transport, expiry, reload, throttling and stale response checks PASS");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
