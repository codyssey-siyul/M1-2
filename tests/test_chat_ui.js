// Offline chat checks: duplicate submissions, IME, safe errors and retry recovery.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element() {
    return {
        value: "", disabled: false, children: [], listeners: {},
        classList: { add() {}, remove() {} },
        appendChild(child) { this.children.push(child); },
        addEventListener(name, listener) {
            (this.listeners[name] ||= []).push(listener);
        },
        focus() {},
        set innerHTML(value) { if (value === "") this.children = []; },
        replaceChildren() { this.children = []; },
    };
}

async function run() {
    const nodes = new Map();
    const getNode = id => {
        if (!nodes.has(id)) nodes.set(id, element());
        return nodes.get(id);
    };
    let calls = 0;
    let respond;
    const events = {};
    const context = vm.createContext({
        console,
        document: {
            addEventListener(name, listener) { events[name] = listener; },
            getElementById: getNode,
            querySelector: getNode,
            querySelectorAll: () => [],
            createElement: element,
        },
        fetch: async url => {
            if (url === "/api/conversations") {
                return { ok: true, json: async () => ({ conversations: [] }) };
            }
            if (url === "/api/conversations/saved") {
                return { ok: true, json: async () => ({ messages: [
                    { role: "user", content: "저장된 질문" },
                    { role: "assistant", content: "저장된 답변" },
                ] }) };
            }
            calls++;
            return new Promise(resolve => { respond = resolve; });
        },
    });
    context.Auth = { fetch: (...args) => context.fetch(...args), ready: Promise.resolve(true) };
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "script.js"), "utf8"), context);
    const input = getNode("user-input");
    const send = getNode("send-button");
    const chat = getNode("chat-box");
    // Login event and restored-session readiness must initialize exactly once.
    events["m1-authenticated"]();
    await Promise.resolve();
    assert.equal(chat.children.length, 1);
    assert.equal(chat.children[0].textContent, "AI 비서: 안녕하세요! 무엇이 궁금하신가요?");
    await context.loadConversation("saved");
    events["m1-authenticated"]();
    assert.deepEqual(chat.children.map(node => node.textContent), ["나: 저장된 질문", "AI 비서: 저장된 답변"]);
    for (const listener of getNode("new-chat-button").listeners.click) listener();
    assert.equal(chat.children.length, 1);
    assert.equal(chat.children[0].textContent, "AI 비서: 안녕하세요! 무엇이 궁금하신가요?");
    events["m1-locked"]();
    assert.equal(chat.children.length, 0);
    events["m1-authenticated"]();
    assert.equal(chat.children.length, 1);
    input.value = "질문";
    const pending = context.sendMessage();
    assert.equal(send.disabled, true);
    input.value = "중복 질문";
    await context.sendMessage();
    assert.equal(calls, 1);
    assert.equal(input.value, "중복 질문");
    respond({ ok: false, json: async () => ({ detail: "대화 길이 한도에 도달했습니다. 새 채팅을 시작해 주세요." }) });
    await pending;
    assert.match(chat.children.at(-1).textContent, /새 채팅/);
    assert.equal(send.disabled, false);
    const afterError = context.sendMessage();
    respond({ ok: true, json: async () => ({ conversation_id: "local", answer: "정상 답변" }) });
    await afterError;
    assert.match(chat.children.at(-1).textContent, /정상 답변/);
    assert.equal(calls, 2);
    input.value = "조합 중";
    for (const listener of input.listeners.keydown) listener({ key: "Enter", isComposing: true });
    assert.equal(calls, 2);
    const invalid = context.sendMessage();
    respond({ ok: false, json: async () => ({ detail: [{ input: "secret-placeholder" }] }) });
    await invalid;
    assert.doesNotMatch(chat.children.at(-1).textContent, /secret-placeholder/);
    assert.match(chat.children.at(-1).textContent, /입력 형식과 길이/);
    assert.equal(send.disabled, false);
    console.log("Chat UI: duplicate request, recovery, IME, safe error checks PASS");
}

run().catch(error => { console.error(error); process.exitCode = 1; });
