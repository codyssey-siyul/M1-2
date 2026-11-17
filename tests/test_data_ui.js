// No browser/network dependencies: run the real UI scripts against a mock DOM/API.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..");

class Element {
    constructor(tag = "div", id = "") {
        this.tagName = tag;
        this.id = id;
        this.children = [];
        this.dataset = {};
        this.listeners = {};
        this.hidden = false;
        this.disabled = false;
        this.value = "";
        this.classList = { add() {}, remove() {} };
    }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren(...children) { this.children = children; }
    set textContent(text) { this.text = String(text); this.children = []; }
    get textContent() { return this.text || this.children.map(child => child.textContent).join(""); }
    set innerHTML(text) { assert.equal(text, "", "UI must not inject HTML"); this.children = []; this.text = ""; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
    focus() {}
    scrollIntoView() {}
    reportValidity() { return true; }
}

function harness(initial = [{ id: "project_001", date: "2026-09-08", value: 100000000, project_type: "로봇 설치", duration_days: 30, memo: "모의 프로젝트" }], settings = {}) {
    const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
    const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element("div", match[1])]));
    nodes.set(".chat-main", new Element());
    nodes.set(".app-layout", new Element());
    const node = id => { assert.ok(nodes.has(id), `Unknown HTML id ${id}`); return nodes.get(id); };
    node("dashboard-year-select").value = "all";
    node("monthly-year-select").value = "2026";
    node("project-form").reset = () => ["project-date", "project-value", "project-type", "project-duration", "project-memo"].forEach(id => { node(id).value = ""; });
    let rows = structuredClone(initial);
    let nextId = 1;
    const calls = [];
    const charts = new Map();
    const replies = new Map();
    const failures = new Map();
    let activeReads = 0;
    let maxActiveReads = 0;
    let confirmResult = false;
    const confirmations = [];
    function yearly() {
        const result = {};
        for (const row of rows) {
            const item = result[row.date.slice(0, 4)] ||= { count: 0, total_value: 0, average_value: 0 };
            item.count++; item.total_value += row.value; item.average_value = Math.floor(item.total_value / item.count);
        }
        return result;
    }
    const response = (payload, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(payload) });
    async function fetchResponse(url, options = {}) {
        assert.ok(url.startsWith("/"), "All UI API requests must remain on the mock origin");
        const method = options.method || "GET";
        calls.push({ url, method, body: options.body && JSON.parse(options.body) });
        const key = method + " " + url;
        if (failures.has(key)) return response(failures.get(key).payload, failures.get(key).status);
        if (replies.has(key)) return replies.get(key)();
        if (url === "/api/conversations") return response({ count: 1, conversations: [{ id: "talk", title: "이전 대화", created_at: "2026-01-01", messages: [] }] });
        if (url === "/api/conversations/talk") return response({ id: "talk", title: "이전 대화", messages: [{ role: "user", content: "이전 질문" }, { role: "assistant", content: "이전 답변" }] });
        if (url === "/api/data" && method === "GET") return response({ count: rows.length, data: rows });
        if (url === "/api/data/summary") {
            const total = rows.reduce((sum, row) => sum + row.value, 0);
            const dates = rows.map(row => row.date).sort();
            return response({ count: rows.length, total_value: total, average_value: rows.length ? Math.round(total / rows.length) : "해당 없음", period: rows.length ? { start: dates[0], end: dates.at(-1) } : null, recent_trend: "모의 추세" });
        }
        if (url === "/api/data/yearly-summary") return response(yearly());
        if (url === "/api/data/monthly-summary") {
            const result = {};
            for (const row of rows) (result[row.date.slice(0, 7)] ||= { total_value: 0 }).total_value += row.value;
            return response(result);
        }
        if (url.startsWith("/api/data/project-type-summary")) {
            const year = new URL(url, "http://mock.invalid").searchParams.get("year");
            const result = {};
            for (const row of rows.filter(row => !year || row.date.startsWith(year))) {
                const item = result[row.project_type] ||= { count: 0, total_value: 0 };
                item.count++; item.total_value += row.value;
            }
            return response(result);
        }
        if (url === "/api/data" && method === "POST") {
            const id = "new_" + nextId++;
            rows.push({ id, ...JSON.parse(options.body) });
            return response({ id, message: "추가 완료" });
        }
        if (url.startsWith("/api/data/") && ["PUT", "DELETE"].includes(method)) {
            const id = decodeURIComponent(url.slice("/api/data/".length));
            const index = rows.findIndex(row => row.id === id);
            if (index < 0) return response({ detail: "해당 프로젝트 데이터를 찾을 수 없습니다." }, 404);
            if (method === "PUT") rows[index] = { id, ...JSON.parse(options.body) };
            else rows.splice(index, 1);
            return response({ id, message: "완료" });
        }
        throw new Error(`Unhandled mock request ${key}`);
    }
    async function fetch(url, options = {}) {
        const isDataRead = (options.method || "GET") === "GET" && url.startsWith("/api/data");
        if (!isDataRead || settings.maxDataReads === undefined) return fetchResponse(url, options);
        ++activeReads;
        maxActiveReads = Math.max(maxActiveReads, activeReads);
        const exceedsBudget = activeReads > settings.maxDataReads;
        try {
            // Overlap real script requests deterministically. The mock returns the
            // same safe 503 used for exhausted session transaction retries.
            await new Promise(setImmediate);
            if (exceedsBudget) {
                calls.push({ url, method: "GET" });
                return response({ detail: "인증 저장소에 연결할 수 없습니다." }, 503);
            }
            return await fetchResponse(url, options);
        } finally { --activeReads; }
    }
    const context = vm.createContext({
        document: {
            addEventListener() {},
            getElementById: node, querySelector: node,
            querySelectorAll(selector) {
                if (selector !== "#project-list button") return [];
                const buttons = [];
                const visit = element => { if (element.tagName === "button") buttons.push(element); element.children.forEach(visit); };
                visit(node("project-list")); return buttons;
            },
            createElement: tag => new Element(tag),
        },
        fetch, console: { error() {} },
        Chart: class {
            constructor(canvas, config) { this.canvas = canvas; this.config = config; charts.set(canvas.id, this); }
            destroy() { if (charts.get(this.canvas.id) === this) charts.delete(this.canvas.id); }
        },
        confirm: message => { confirmations.push(message); return confirmResult; }, alert() {},
    });
    context.Auth = { fetch: (...args) => fetch(...args), ready: Promise.resolve(true) };
    for (const script of ["script.js", "data-manager.js"]) vm.runInContext(fs.readFileSync(path.join(root, script), "utf8"), context);
    const setForm = overrides => {
        const row = { date: "2027-02-01", value: 200000000, project_type: "테스트 유형", duration_days: 20, memo: "추가 메모", ...overrides };
        for (const [id, key] of [["project-date", "date"], ["project-value", "value"], ["project-type", "project_type"], ["project-duration", "duration_days"], ["project-memo", "memo"]]) node(id).value = String(row[key]);
    };
    return { context, node, calls, charts, failures, replies, confirmations, setForm,
        get rows() { return rows; }, get maxActiveReads() { return maxActiveReads; },
        setRows(value) { rows = value; }, confirm(value) { confirmResult = value; } };
}

async function run() {
    // Reproduce the post-write five-GET burst against an auth storage contention
    // condition. Writes still succeed, so this must not produce a refresh warning.
    const constrained = harness(undefined, { maxDataReads: 3 });
    await constrained.context.loadManagedData();
    constrained.setForm();
    await constrained.context.submitProject({ preventDefault() {} });
    assert.equal(constrained.rows.length, 2);
    assert.equal(constrained.node("data-status").dataset.kind, "success", "POST committed, but concurrent refresh produced a false-looking failure notice");
    const committedAt = constrained.calls.findIndex(call => call.method === "POST");
    assert.deepEqual(constrained.calls.slice(committedAt + 1).filter(call => call.method === "GET").map(call => call.url), [
        "/api/data", "/api/data/summary", "/api/data/yearly-summary", "/api/data/monthly-summary", "/api/data/project-type-summary"
    ]);
    const inserted = constrained.rows.find(row => row.id.startsWith("new_"));
    constrained.context.editProject(inserted);
    constrained.setForm({ value: 400000000 });
    await constrained.context.submitProject({ preventDefault() {} });
    assert.equal(constrained.node("data-status").dataset.kind, "success");
    assert.equal(constrained.node("data-total-value").textContent, "500,000,000원");
    constrained.confirm(true);
    await constrained.context.deleteProject(inserted);
    assert.equal(constrained.node("data-status").dataset.kind, "success");
    assert.equal(constrained.node("data-total-value").textContent, "100,000,000원");
    assert.equal(constrained.charts.size, 4);
    assert.equal(constrained.calls.filter(call => call.method !== "GET").length, 3);
    assert.equal(constrained.maxActiveReads, 1, "Refresh must not contend on the same authenticated session document");

    const h = harness();
    const c = h.context;
    assert.equal(await c.loadManagedData(), true);
    assert.equal(h.node("project-list").children.length, 1);
    assert.equal(h.node("data-total-count").textContent, "1건");
    h.node("dashboard-year-select").value = "2026";
    h.setForm();
    await c.submitProject({ preventDefault() {} });
    assert.equal(h.rows.length, 2);
    assert.equal(h.node("data-total-value").textContent, "300,000,000원");
    assert.equal(h.node("project-list").children.length, 2);
    assert.equal(h.node("dashboard-year-select").value, "2026");
    assert.equal(h.node("monthly-year-select").value, "2026");
    assert.ok(h.node("dashboard-year-select").children.some(option => option.value === "2027"));
    assert.equal(h.charts.size, 4);
    assert.equal(h.node("total-order-value").textContent, "100,000,000원");
    assert.equal(h.charts.get("project-type-count-chart").config.data.datasets[0].data[0], 1);
    const added = h.rows.find(row => row.id.startsWith("new_"));
    c.editProject(added);
    assert.equal(h.node("project-memo").value, "추가 메모");
    assert.equal(h.node("project-cancel").hidden, false);
    h.setForm({ value: 400000000, memo: "수정 메모" });
    await c.submitProject({ preventDefault() {} });
    assert.equal(h.rows.length, 2);
    assert.equal(h.rows.find(row => row.id === added.id).value, 400000000);
    assert.equal(h.node("data-total-value").textContent, "500,000,000원");
    assert.equal(h.node("project-cancel").hidden, true);
    h.confirm(false);
    const writes = h.calls.filter(call => call.method !== "GET").length;
    await c.deleteProject(added);
    assert.equal(h.calls.filter(call => call.method !== "GET").length, writes);
    assert.match(h.confirmations.at(-1), /삭제하시겠습니까/);
    h.confirm(true);
    c.editProject(added);
    await c.deleteProject(added);
    assert.equal(h.rows.length, 1);
    assert.equal(h.node("data-total-count").textContent, "1건");
    assert.equal(h.node("project-cancel").hidden, true);
    assert.equal(h.charts.size, 4);

    // Empty state is distinct from failure; remove all rows without a real DB.
    await c.deleteProject(h.rows[0]);
    assert.equal(h.node("project-table").hidden, true);
    assert.match(h.node("data-list-state").textContent, /첫 프로젝트/);
    assert.equal(h.node("data-total-value").textContent, "0원");
    assert.equal(h.node("data-average-value").textContent, "해당 없음");
    assert.match(h.node("dashboard-status").textContent, /등록된 수주 데이터가 없습니다/);

    // Failed writes retain form values and never become success notices.
    h.setForm();
    h.failures.set("POST /api/data", { status: 422, payload: { detail: [{ input: "secret-placeholder" }] } });
    await c.submitProject({ preventDefault() {} });
    assert.equal(h.rows.length, 0);
    assert.equal(h.node("project-memo").value, "추가 메모");
    assert.equal(h.node("project-fields").disabled, false);
    assert.equal(h.node("data-status").dataset.kind, "error");
    assert.doesNotMatch(h.node("data-status").textContent, /secret-placeholder/);
    h.failures.clear();
    for (const values of [{ memo: "  " }, { value: 0 }, { duration_days: 1.5 }, { date: "2023-02-29" }]) {
        h.setForm(values);
        const before = h.calls.length;
        await c.submitProject({ preventDefault() {} });
        assert.equal(h.calls.length, before);
    }

    // A successful write followed by a failed read must not invite a duplicate write.
    h.setForm();
    h.failures.set("GET /api/data", { status: 503, payload: { detail: "Firestore 연결을 확인해 주세요." } });
    await c.submitProject({ preventDefault() {} });
    assert.equal(h.rows.length, 1);
    assert.match(h.node("data-status").textContent, /추가했습니다.*같은 작업을 반복하지 말고/);
    assert.equal(h.node("project-memo").value, "");
    assert.match(h.node("data-list-state").textContent, /불러오지 못했습니다/);
    h.failures.clear();
    assert.equal(await c.loadManagedData(), true);
    assert.equal(h.node("project-list").children.length, 1);

    // Text content is rendered literally, never as HTML.
    const hostile = { ...h.rows[0], project_type: "<img src=x onerror=alert(1)>", memo: "<script>unsafe</script>" };
    c.renderManagedList([hostile]);
    assert.equal(h.node("project-list").children[0].children[4].textContent, hostile.memo);

    // Navigation back to a stored conversation still restores the chat page.
    c.showPage("data");
    await c.loadConversation("talk");
    assert.equal(h.node("data-page").hidden, true);
    assert.equal(h.node(".chat-main").hidden, false);
    assert.match(h.node("chat-box").textContent, /이전 답변/);

    // Double submit is blocked while the first mock write is pending.
    const h2 = harness([]);
    h2.setForm();
    let release;
    h2.replies.set("POST /api/data", () => new Promise(resolve => { release = resolve; }));
    const pending = h2.context.submitProject({ preventDefault() {} });
    await h2.context.submitProject({ preventDefault() {} });
    assert.equal(h2.calls.filter(call => call.method === "POST").length, 1);
    release({ ok: true, status: 200, json: async () => ({ id: "pending" }) });
    await pending;
    assert.equal(h2.node("project-fields").disabled, false);

    // A later refresh wins even when the older read resolves last.
    let older;
    h2.replies.set("GET /api/data", () => new Promise(resolve => { older = resolve; }));
    const oldRead = h2.context.loadManagedData();
    await new Promise(setImmediate);
    h2.replies.delete("GET /api/data");
    h2.setRows([hostile]);
    const latestRead = h2.context.loadManagedData();
    older({ ok: true, json: async () => ({ count: 0, data: [] }) });
    await Promise.all([oldRead, latestRead]);
    assert.equal(h2.node("project-list").children.length, 1);
    console.log("Data UI: post-CRUD contention regression, serialized refresh order/recovery, mock CRUD, filters, 4 charts, errors, conversation and stale request checks PASS");
}

run().catch(error => { console.error(error); process.exitCode = 1; });
