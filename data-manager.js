// All requests use the existing backend API. Tests replace fetch with an in-memory API.
const dataPage = document.getElementById("data-page");
const projectForm = document.getElementById("project-form");
const projectFields = document.getElementById("project-fields");
const dataStatus = document.getElementById("data-status");
let managedProjects = [];
let editingProjectId = null;
let dataBusy = false;
let dataRequestVersion = 0;

function setDataStatus(message, kind = "info") {
    dataStatus.textContent = message;
    dataStatus.dataset.kind = kind;
}

function setDataBusy(busy) {
    dataBusy = busy;
    projectFields.disabled = busy;
    document.getElementById("data-refresh").disabled = busy;
    document.querySelectorAll("#project-list button").forEach(button => { button.disabled = busy; });
    document.getElementById("project-save").textContent = busy ? "처리 중..." : editingProjectId ? "수정 저장" : "프로젝트 추가";
}

function resetProjectForm() {
    editingProjectId = null;
    projectForm.reset();
    document.getElementById("project-form-title").textContent = "새 프로젝트 추가";
    document.getElementById("project-save").textContent = "프로젝트 추가";
    document.getElementById("project-cancel").hidden = true;
}

function editProject(project) {
    if (dataBusy) return;
    if (project.read_only) { setDataStatus("기존 실적 데이터는 읽기 전용입니다.", "error"); return; }
    editingProjectId = project.id;
    for (const [id, key] of [["project-date", "date"], ["project-value", "value"],
        ["project-type", "project_type"], ["project-duration", "duration_days"], ["project-memo", "memo"]]) {
        document.getElementById(id).value = project[key];
    }
    document.getElementById("project-form-title").textContent = "프로젝트 수정";
    document.getElementById("project-save").textContent = "수정 저장";
    document.getElementById("project-cancel").hidden = false;
    setDataStatus("프로젝트를 수정한 후 ‘수정 저장’을 눌러 주세요.");
    projectForm.scrollIntoView({ behavior: "smooth", block: "start" });
    document.getElementById("project-date").focus({ preventScroll: true });
}

function renderManagedList(projects) {
    const list = document.getElementById("project-list");
    list.replaceChildren();
    document.getElementById("data-list-count").textContent = `(${projects.length.toLocaleString("ko-KR")}건)`;
    document.getElementById("project-table").hidden = projects.length === 0;
    document.getElementById("data-list-state").textContent = projects.length
        ? "최근 수주일부터 표시됩니다. 수정 또는 삭제할 프로젝트를 선택하세요."
        : "등록된 수주 프로젝트가 없습니다. 위 입력란에서 첫 프로젝트를 추가해 주세요.";
    const sorted = [...projects].sort((a, b) => b.date.localeCompare(a.date) || a.id.localeCompare(b.id));
    for (const project of sorted) {
        const row = document.createElement("tr");
        const values = [project.date, Number(project.value).toLocaleString("ko-KR") + "원",
            project.project_type, Number(project.duration_days).toLocaleString("ko-KR") + "일", project.memo];
        const labels = ["수주일", "수주금액", "프로젝트 유형", "작업 기간", "메모"];
        values.forEach((value, index) => {
            const cell = document.createElement("td");
            cell.dataset.label = labels[index];
            cell.textContent = value;
            row.appendChild(cell);
        });
        const actionsCell = document.createElement("td");
        actionsCell.dataset.label = "관리";
        const actions = document.createElement("div");
        actions.className = "project-row-actions";
        if (project.read_only) {
            const note = document.createElement("span");
            note.className = "readonly-note";
            note.textContent = "원본 · 읽기 전용";
            actionsCell.appendChild(note);
            row.appendChild(actionsCell);
            list.appendChild(row);
            continue;
        }
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "secondary-button";
        edit.textContent = "수정";
        edit.setAttribute("aria-label", `${project.date} ${project.project_type} 프로젝트 수정`);
        edit.disabled = dataBusy;
        edit.addEventListener("click", () => editProject(project));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "delete-project";
        remove.textContent = "삭제";
        remove.setAttribute("aria-label", `${project.date} ${project.project_type} 프로젝트 삭제`);
        remove.disabled = dataBusy;
        remove.addEventListener("click", () => deleteProject(project));
        actions.appendChild(edit);
        actions.appendChild(remove);
        actionsCell.appendChild(actions);
        row.appendChild(actionsCell);
        list.appendChild(row);
    }
    const types = document.getElementById("project-types");
    types.replaceChildren();
    [...new Set(projects.map(project => project.project_type))].sort().forEach(type => {
        const option = document.createElement("option");
        option.value = type;
        types.appendChild(option);
    });
}

function syncDashboardYears(projects) {
    for (const id of ["dashboard-year-select", "monthly-year-select"]) {
        const select = document.getElementById(id);
        const selected = select.value;
        const years = new Set(["2022", "2023", "2024", "2025", "2026", ...projects.map(project => project.date.slice(0, 4))]);
        if (selected && selected !== "all") years.add(selected);
        select.replaceChildren();
        if (id === "dashboard-year-select") {
            const option = document.createElement("option");
            option.value = "all";
            option.textContent = "전체 기간";
            select.appendChild(option);
        }
        [...years].sort().reverse().forEach(year => {
            const option = document.createElement("option");
            option.value = year;
            option.textContent = `${year}년`;
            select.appendChild(option);
        });
        select.value = selected || (id === "dashboard-year-select" ? "all" : "2026");
    }
}

function renderManagedSummary(summary) {
    document.getElementById("data-total-value").textContent = Number(summary.total_value).toLocaleString("ko-KR") + "원";
    document.getElementById("data-total-count").textContent = Number(summary.count).toLocaleString("ko-KR") + "건";
    document.getElementById("data-average-value").textContent = typeof summary.average_value === "number"
        ? summary.average_value.toLocaleString("ko-KR") + "원" : "해당 없음";
    document.getElementById("data-period").textContent = summary.period
        ? `데이터 기간: ${summary.period.start} ~ ${summary.period.end}` : "데이터 기간: 해당 없음";
    document.getElementById("data-trend").textContent = `최근 추세 (마지막 데이터 월 기준): ${summary.recent_trend}`;
}

async function loadManagedData() {
    const version = ++dataRequestVersion;
    setDataStatus("데이터를 불러오는 중입니다...");
    document.getElementById("data-list-state").textContent = "목록을 불러오는 중입니다...";
    try {
        const [result, summary] = await Promise.all([readApi("/api/data"), readApi("/api/data/summary")]);
        if (version !== dataRequestVersion) return false;
        managedProjects = result.data;
        renderManagedList(managedProjects);
        renderManagedSummary(summary);
        syncDashboardYears(managedProjects);
        setDataStatus("");
        return true;
    } catch (error) {
        if (version !== dataRequestVersion) return false;
        managedProjects = [];
        renderManagedList([]);
        document.getElementById("data-list-count").textContent = "";
        document.getElementById("data-list-state").textContent = "목록을 불러오지 못했습니다. ‘새로고침’으로 다시 시도해 주세요.";
        for (const id of ["data-total-value", "data-total-count", "data-average-value"]) {
            document.getElementById(id).textContent = "-";
        }
        document.getElementById("data-period").textContent = "데이터 기간: -";
        document.getElementById("data-trend").textContent = "최근 추세: -";
        setDataStatus(error.message, "error");
        return false;
    }
}

function projectFormData() {
    const date = document.getElementById("project-date").value;
    const value = Number(document.getElementById("project-value").value);
    const duration = Number(document.getElementById("project-duration").value);
    const type = document.getElementById("project-type").value.trim();
    const memo = document.getElementById("project-memo").value.trim();
    const parsed = new Date(date + "T00:00:00Z");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(parsed.getTime()) ||
        parsed.toISOString().slice(0, 10) !== date || date < "1900-01-01" || date > "2100-12-31" ||
        !Number.isSafeInteger(value) || value < 1 || value > 1e15 ||
        !Number.isInteger(duration) || duration < 1 || duration > 36500 ||
        !type || type.length > 100 || !memo || memo.length > 2000) {
        throw new Error("날짜와 양의 정수 금액·기간을 확인하고, 유형과 메모를 공백 없이 입력해 주세요.");
    }
    return { date, value, project_type: type, duration_days: duration, memo };
}

async function refreshAfterDataChange(message) {
    // Rebuild the list/summary and year options before drawing the dashboard.
    const listOk = await loadManagedData();
    const dashboardOk = await refreshDashboard();
    setDataStatus(listOk && dashboardOk ? message
        : `${message} 일부 화면을 갱신하지 못했습니다. 같은 작업을 반복하지 말고 새로고침해 주세요.`,
    listOk && dashboardOk ? "success" : "error");
}

async function submitProject(event) {
    event.preventDefault();
    if (dataBusy || !projectForm.reportValidity()) return;
    let payload;
    try { payload = projectFormData(); }
    catch (error) { setDataStatus(error.message, "error"); return; }
    const id = editingProjectId;
    ++dataRequestVersion; // Discard any older list response while a mutation is in flight.
    setDataBusy(true);
    setDataStatus(id ? "수정 내용을 저장하는 중입니다..." : "프로젝트를 추가하는 중입니다...");
    try {
        await readApi(id ? `/api/data/${encodeURIComponent(id)}` : "/api/data", {
            method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
        });
        resetProjectForm();
        await refreshAfterDataChange(id ? "프로젝트를 수정했습니다." : "프로젝트를 추가했습니다.");
    } catch (error) {
        if (error.status === 404) {
            await loadManagedData();
            resetProjectForm();
        }
        setDataStatus(`${error.message} 저장 결과가 불확실하면 새로고침으로 목록을 먼저 확인해 주세요.`, "error");
    } finally {
        setDataBusy(false);
    }
}

async function deleteProject(project) {
    if (dataBusy) return;
    if (project.read_only) { setDataStatus("기존 실적 데이터는 삭제할 수 없습니다.", "error"); return; }
    if (!confirm(`${project.date} / ${project.project_type} / ${Number(project.value).toLocaleString("ko-KR")}원\n이 프로젝트를 삭제하시겠습니까? 삭제 후에는 복구할 수 없습니다.`)) return;
    ++dataRequestVersion;
    setDataBusy(true);
    setDataStatus("프로젝트를 삭제하는 중입니다...");
    try {
        await readApi(`/api/data/${encodeURIComponent(project.id)}`, { method: "DELETE" });
        if (editingProjectId === project.id) resetProjectForm();
        await refreshAfterDataChange("프로젝트를 삭제했습니다.");
    } catch (error) {
        if (error.status === 404) {
            if (editingProjectId === project.id) resetProjectForm();
            await loadManagedData();
        }
        setDataStatus(`${error.message} 새로고침으로 현재 목록을 확인해 주세요.`, "error");
    } finally {
        setDataBusy(false);
    }
}

document.getElementById("data-button").addEventListener("click", () => {
    showPage("data");
    if (!dataBusy) loadManagedData();
});
document.getElementById("data-refresh").addEventListener("click", async () => {
    if (dataBusy) return;
    const listOk = await loadManagedData();
    const dashboardOk = await refreshDashboard();
    if (listOk && !dashboardOk) setDataStatus("목록을 갱신했습니다. 그래프 갱신에 실패했습니다. 다시 새로고침해 주세요.", "error");
});
document.getElementById("project-cancel").addEventListener("click", () => {
    if (!dataBusy) { resetProjectForm(); setDataStatus("수정을 취소했습니다."); }
});
projectForm.addEventListener("submit", submitProject);
document.addEventListener("m1-locked", () => {
    ++dataRequestVersion;
    managedProjects = [];
    resetProjectForm();
    renderManagedList([]);
    for (const id of ["data-total-value", "data-total-count", "data-average-value"]) {
        document.getElementById(id).textContent = "-";
    }
    document.getElementById("data-period").textContent = "데이터 기간: -";
    document.getElementById("data-trend").textContent = "최근 추세: -";
    setDataStatus("");
});
