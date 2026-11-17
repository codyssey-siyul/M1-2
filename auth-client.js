// Session cookie stays HttpOnly; only the derived CSRF token lives in JS memory.
globalThis.Auth = (() => {
    let csrf = "";
    let generation = 0;
    let signedIn = false;
    const loginPage = document.getElementById("login-page");
    const layout = document.querySelector(".app-layout");
    const status = document.getElementById("login-status");
    const logoutButton = document.getElementById("logout-button");

    function lock(message = "로그인 후 실적 데이터와 AI 비서를 이용할 수 있습니다.") {
        generation++;
        signedIn = false;
        csrf = "";
        layout.hidden = true;
        loginPage.hidden = false;
        logoutButton.hidden = true;
        document.getElementById("account-name").textContent = "";
        status.textContent = message;
        document.dispatchEvent(new Event("m1-locked"));
    }

    function unlock(data) {
        generation++;
        csrf = data.csrf_token;
        signedIn = true;
        loginPage.hidden = true;
        layout.hidden = false;
        logoutButton.hidden = false;
        document.getElementById("account-name").textContent = data.username;
        status.textContent = "";
        document.dispatchEvent(new Event("m1-authenticated"));
    }

    async function securedFetch(url, options = {}) {
        if (!signedIn) throw new Error("로그인이 필요합니다.");
        const version = generation;
        const method = (options.method || "GET").toUpperCase();
        const headers = { ...options.headers };
        if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = csrf;
        const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
        if (version !== generation) throw new Error("로그인 상태가 변경되었습니다.");
        if (response.status === 401) {
            lock("세션이 만료되었습니다. 다시 로그인해 주세요.");
            throw new Error("세션이 만료되었습니다. 다시 로그인해 주세요.");
        }
        const parse = response.json.bind(response);
        response.json = async () => {
            const payload = await parse();
            if (version !== generation) throw new Error("로그인 상태가 변경되었습니다.");
            return payload;
        };
        return response;
    }

    async function initialize() {
        const version = generation;
        try {
            const response = await fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" });
            if (version !== generation) return signedIn;
            if (response.ok) { unlock(await response.json()); return true; }
            lock(response.status === 503 ? "서버 인증 설정 또는 연결을 확인해 주세요." : undefined);
        } catch {
            if (version !== generation) return signedIn;
            lock("서버에 연결하지 못했습니다. 잠시 후 로그인해 주세요.");
        }
        return false;
    }

    document.getElementById("login-form").addEventListener("submit", async event => {
        event.preventDefault();
        const button = document.getElementById("login-button");
        if (button.disabled) return;
        generation++;
        button.disabled = true;
        status.textContent = "로그인 중입니다. 서버가 쉬고 있었다면 준비에 약 1분이 걸릴 수 있습니다.";
        try {
            const response = await fetch("/api/auth/login", {
                method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: document.getElementById("login-username").value,
                    password: document.getElementById("login-password").value })
            });
            const data = await response.json().catch(() => null);
            if (!response.ok) {
                status.textContent = typeof data?.detail === "string" ? data.detail : "로그인 정보를 확인해 주세요.";
                if (response.status === 429) status.textContent += ` ${response.headers.get("Retry-After") || "잠시"}초 후 다시 시도해 주세요.`;
                return;
            }
            unlock(data);
        } catch {
            status.textContent = "로그인 결과를 확인하지 못했습니다. 서버 준비 후 다시 시도해 주세요.";
        } finally {
            document.getElementById("login-password").value = "";
            button.disabled = false;
        }
    });

    logoutButton.addEventListener("click", async () => {
        if (logoutButton.disabled) return;
        logoutButton.disabled = true;
        try {
            const response = await securedFetch("/api/auth/logout", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
            });
            if (!response.ok) throw new Error();
            lock("로그아웃했습니다.");
        } catch {
            if (signedIn) document.getElementById("account-name").textContent = "로그아웃 실패 · 다시 눌러 주세요";
        } finally { logoutButton.disabled = false; }
    });

    return { fetch: securedFetch, ready: initialize() };
})();
