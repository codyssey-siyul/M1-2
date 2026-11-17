const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const newChatButton = document.getElementById("new-chat-button");
const conversationList = document.getElementById("conversation-list");

let conversationId = null;
let chatSession = 0;

// 채팅 메시지를 화면에 표시
function addMessage(sender, text) {
    const message = document.createElement("p");
    message.textContent = `${sender}: ${text}`;

    // 사용자와 AI 메시지 구분
    message.className = sender === "나" ? "user-message" : "ai-message";

    chatBox.appendChild(message);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// 사용자 질문을 AI 서버로 전송
async function sendMessage() {
    if (sendButton.disabled) return;

    const question = userInput.value.trim();

    if (!question) return;

    addMessage("나", question);
    userInput.value = "";

    sendButton.disabled = true;
    sendButton.textContent = "답변 중...";
    let failureMessage = "AI 서버에 연결할 수 없습니다.";

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: question,
                conversation_id: conversationId
            })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => null);
            failureMessage = typeof errorData?.detail === "string"
                ? errorData.detail
                : "질문 또는 대화 정보의 입력 형식과 길이를 확인해 주세요.";
            throw new Error("서버 응답 오류");
        }

        const data = await response.json();

        conversationId = data.conversation_id;
        addMessage("AI 비서", data.answer);

        // AI 답변 완료 후 이전 대화 목록 갱신
        loadConversations();

    } catch (error) {
        addMessage("시스템", failureMessage);

    } finally {
        sendButton.disabled = false;
        sendButton.textContent = "전송";
        userInput.focus();
    }
}

// 새 채팅 버튼 클릭
newChatButton.addEventListener("click", function() {
    if (sendButton.disabled) return;

    chatSession++;
    conversationId = null;

    // 새 채팅 시작 시 기존 선택 표시 해제
    document.querySelectorAll(".conversation-row.active").forEach(function(item) {
        item.classList.remove("active");
    });

    chatBox.innerHTML = "";
    addMessage("AI 비서", "안녕하세요! 무엇이 궁금하신가요?");

    userInput.value = "";
    userInput.focus();
});

// 전송 버튼 클릭
sendButton.addEventListener("click", sendMessage);

// Enter 키로 전송
userInput.addEventListener("keydown", function(event) {
    if (event.key === "Enter" && !event.isComposing) {
        sendMessage();
    }
});

// 이전 대화 목록 불러오기
async function loadConversations() {
    try {
        const response = await fetch("/api/conversations");

        if (!response.ok) {
            throw new Error("대화 목록 조회 실패");
        }

        const data = await response.json();

        conversationList.innerHTML = "";

        // 대화 생성일 기준 최신순 정렬
        data.conversations
            .sort(function(a, b) {
                return new Date(b.created_at) - new Date(a.created_at);
            })
            .forEach(function(conversation) {

            const button = document.createElement("button");

            button.textContent = conversation.title || "제목 없는 대화";
            button.className = "conversation-item";
            button.dataset.id = conversation.id;

            // 이전 대화 클릭 시 불러오기
            button.addEventListener("click", function() {
                loadConversation(conversation.id);
            });

            // 대화 항목을 감싸는 영역
            const item = document.createElement("div");
            item.className = "conversation-row";

            // 현재 선택한 대화 강조 표시
            if (conversationId === conversation.id) {
                item.classList.add("active");
            }

            // 삭제 버튼 생성
            const deleteButton = document.createElement("button");
            deleteButton.className = "conversation-delete";
            deleteButton.textContent = "×";
            deleteButton.title = "대화 삭제";
            deleteButton.type = "button";

            // 삭제 버튼 클릭 시
            deleteButton.addEventListener("click", async function(event) {
                event.stopPropagation();

                const confirmed = confirm(
                    "이 대화를 삭제하시겠습니까?\n삭제한 대화는 복구할 수 없습니다."
                );

                if (!confirmed) return;

                deleteButton.disabled = true;

                try {
                    const response = await fetch(
                        `/api/conversations/${encodeURIComponent(conversation.id)}`,
                        {
                            method: "DELETE"
                        }
                    );

                    if (!response.ok) {
                        throw new Error("대화 삭제 실패");
                    }

                    // 현재 보고 있는 대화를 삭제한 경우
                    if (conversationId === conversation.id) {
                        conversationId = null;
                        chatBox.innerHTML = "";
                        addMessage("AI 비서", "안녕하세요! 무엇이 궁금하신가요?");
                        userInput.value = "";
                    }

                    // 삭제 후 목록 갱신
                    await loadConversations();

                } catch (error) {
                    console.error("대화 삭제 오류:", error);
                    alert("대화를 삭제하지 못했습니다.");
                    deleteButton.disabled = false;
                }
            });

            // 대화 제목과 삭제 버튼을 함께 배치
            item.appendChild(button);
            item.appendChild(deleteButton);

            conversationList.appendChild(item);
        });

    } catch (error) {
        console.error("대화 목록 오류:", error);
    }
}

// 선택한 이전 대화 불러오기
async function loadConversation(id) {
    if (sendButton.disabled) return;

    try {
        const response = await fetch(
            `/api/conversations/${encodeURIComponent(id)}`
        );

        if (!response.ok) {
            throw new Error("대화 불러오기 실패");
        }

        const data = await response.json();

        // 이전 대화를 열면 어느 메뉴에서든 채팅 화면으로 전환
        showPage("chat");
        conversationId = id;

        // 선택한 대화 표시 갱신
        loadConversations();

        // 기존 채팅 화면 초기화
        chatBox.innerHTML = "";

        // 저장된 메시지 표시
        data.messages.forEach(function(message) {
            const sender = message.role === "user" ? "나" : "AI 비서";
            addMessage(sender, message.content);
        });

        userInput.value = "";
        userInput.focus();

    } catch (error) {
        console.error("대화 불러오기 오류:", error);
        alert("이전 대화를 불러오지 못했습니다.");
    }
}

// 웹페이지 시작 시 이전 대화 목록 불러오기
loadConversations();

// 채팅 화면과 대시보드 화면 전환
const dashboardButton = document.getElementById("dashboard-button");
const dashboardPage = document.getElementById("dashboard-page");
const chatMain = document.querySelector(".chat-main");

function showPage(page) {
    chatMain.hidden = page !== "chat";
    dashboardPage.hidden = page !== "dashboard";
    document.getElementById("data-page").hidden = page !== "data";
    const layout = document.querySelector(".app-layout");
    if (page === "data") layout.classList.add("data-view");
    else layout.classList.remove("data-view");
}

dashboardButton.addEventListener("click", function() {
    showPage("dashboard");
    refreshDashboard();
});
newChatButton.addEventListener("click", function() {
    if (!sendButton.disabled) showPage("chat");
});

// 대시보드 전체 실적 요약 불러오기
async function loadDashboardSummary(yearlyData = null) {
    try {
        if (yearlyData === null) yearlyData = await readApi("/api/data/yearly-summary");

        // 대시보드에서 선택한 연도
        const selectedYear = document.getElementById(
            "dashboard-year-select"
        ).value;

        let totalValue = 0;
        let totalCount = 0;

        if (selectedYear === "all") {
            // 전체 기간 실적 합산
            Object.values(yearlyData).forEach(function(year) {
                totalValue += year.total_value;
                totalCount += year.count;
            });
        } else {
            // 선택한 연도 실적만 계산
            const yearData = yearlyData[selectedYear];

            if (yearData) {
                totalValue = yearData.total_value;
                totalCount = yearData.count;
            }
        }

        // 평균 수주금액 계산
        const averageValue = totalCount > 0
            ? Math.round(totalValue / totalCount)
            : null;

        // 화면에 실적 표시
        document.getElementById("total-order-value").textContent =
            totalValue.toLocaleString("ko-KR") + "원";

        document.getElementById("total-order-count").textContent =
            totalCount.toLocaleString("ko-KR") + "건";

        document.getElementById("average-order-value").textContent =
            averageValue === null
                ? "해당 없음"
                : averageValue.toLocaleString("ko-KR") + "원";

        return true;
    } catch (error) {
        console.error("대시보드 실적 조회 오류:", error);
        return false;
    }
}

// 대시보드 버튼 클릭 시 실적 데이터 불러오기


// 연도별 수주금액 그래프
let yearlyChart = null;

async function loadYearlyChart(yearlyData = null) {
    try {
        if (yearlyData === null) yearlyData = await readApi("/api/data/yearly-summary");

        // 연도 및 수주금액 추출
        const years = Object.keys(yearlyData).sort();
        const values = years.map(function(year) {
            return yearlyData[year].total_value / 100000000;
        });

        const canvas = document.getElementById("yearly-chart");

        // 기존 그래프가 있다면 삭제 (중복 생성 방지)
        if (yearlyChart !== null) {
            yearlyChart.destroy();
        }

        // 막대그래프 생성
        yearlyChart = new Chart(canvas, {
            type: "bar",
            data: {
                labels: years,
                datasets: [{
                    label: "수주금액 (억 원)",
                    data: values,
                    backgroundColor: "#41658a",
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: "수주금액 (억 원)"
                        }
                    }
                }
            }
        });

        return true;
    } catch (error) {
        console.error("연도별 그래프 오류:", error);
        return false;
    }
}

// 대시보드 클릭 시 연도별 그래프 불러오기


// 월별 수주금액 추이 그래프
let monthlyChart = null;

async function loadMonthlyChart(monthlyData = null) {
    try {
        if (monthlyData === null) monthlyData = await readApi("/api/data/monthly-summary");
        const selectedYear = document.getElementById("monthly-year-select").value;

        // 선택한 연도의 1월~12월 수주금액
        const months = [];
        const values = [];

        for (let month = 1; month <= 12; month++) {
            const monthKey = `${selectedYear}-${String(month).padStart(2, "0")}`;

            months.push(`${month}월`);
            values.push(
                (monthlyData[monthKey]?.total_value ?? 0) / 100000000
            );
        }

        const canvas = document.getElementById("monthly-chart");

        // 기존 그래프 제거
        if (monthlyChart !== null) {
            monthlyChart.destroy();
        }

        // 월별 수주금액 꺾은선그래프
        monthlyChart = new Chart(canvas, {
            type: "line",
            data: {
                labels: months,
                datasets: [{
                    label: "월별 수주금액 (억 원)",
                    data: values,
                    borderColor: "#41658a",
                    backgroundColor: "rgba(65, 101, 138, 0.15)",
                    borderWidth: 2,
                    tension: 0.3,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: "수주금액 (억 원)"
                        }
                    }
                }
            }
        });

        return true;
    } catch (error) {
        console.error("월별 그래프 오류:", error);
        return false;
    }
}

// 대시보드 진입 시 월별 그래프 표시


// 월별 그래프의 독립 연도 필터 유지
document.getElementById("monthly-year-select").addEventListener("change", refreshDashboard);

// 프로젝트 유형별 수주금액 그래프
let projectTypeValueChart = null;

async function loadProjectTypeValueChart(projectData = null) {
    try {
        // 대시보드에서 선택한 연도 확인
        const selectedYear = document.getElementById(
            "dashboard-year-select"
        ).value;

        // 선택한 연도에 맞는 API 주소 생성
        const url = selectedYear === "all"
            ? "/api/data/project-type-summary"
            : `/api/data/project-type-summary?year=${selectedYear}`;

        if (projectData === null) projectData = await readApi(url);

        // 수주금액이 큰 유형부터 정렬
        const sortedData = Object.entries(projectData).sort(
            (a, b) => b[1].total_value - a[1].total_value
        );

        const labels = sortedData.map(item => item[0]);
        const values = sortedData.map(
            item => item[1].total_value / 100000000
        );

        const canvas = document.getElementById("project-type-value-chart");

        if (projectTypeValueChart !== null) {
            projectTypeValueChart.destroy();
        }

        projectTypeValueChart = new Chart(canvas, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "수주금액 (억 원)",
                    data: values,
                    backgroundColor: "#41658a",
                    borderRadius: 5
                }]
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: "수주금액 (억 원)"
                        }
                    }
                }
            }
        });

        return true;
    } catch (error) {
        console.error("프로젝트 유형별 수주금액 그래프 오류:", error);
        return false;
    }
}

// 대시보드 진입 시 프로젝트 유형별 수주금액 그래프 표시


// 프로젝트 유형별 수주건수 그래프
let projectTypeCountChart = null;

async function loadProjectTypeCountChart(projectData = null) {
    try {
        // 대시보드에서 선택한 연도 확인
        const selectedYear = document.getElementById(
            "dashboard-year-select"
        ).value;

        // 전체 기간이면 기존 API, 특정 연도면 연도 조건 추가
        const url = selectedYear === "all"
            ? "/api/data/project-type-summary"
            : `/api/data/project-type-summary?year=${selectedYear}`;

        if (projectData === null) projectData = await readApi(url);

        // 수주건수가 많은 유형부터 정렬
        const sortedData = Object.entries(projectData).sort(
            (a, b) => b[1].count - a[1].count
        );

        const labels = sortedData.map(item => item[0]);
        const counts = sortedData.map(item => item[1].count);

        const canvas = document.getElementById("project-type-count-chart");

        // 기존 그래프 제거
        if (projectTypeCountChart !== null) {
            projectTypeCountChart.destroy();
        }

        projectTypeCountChart = new Chart(canvas, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "수주건수 (건)",
                    data: counts,
                    backgroundColor: "#6b8eae",
                    borderRadius: 5
                }]
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0
                        },
                        title: {
                            display: true,
                            text: "수주건수 (건)"
                        }
                    }
                }
            }
        });

        return true;
    } catch (error) {
        console.error("프로젝트 유형별 수주건수 그래프 오류:", error);
        return false;
    }
}

// 대시보드 진입 시 프로젝트 유형별 수주건수 그래프 표시


document.getElementById("dashboard-year-select").addEventListener("change", refreshDashboard);

async function readApi(url, options) {
    let response;
    try {
        response = await fetch(url, options);
    } catch {
        throw new Error("서버에 연결할 수 없습니다. 연결 상태를 확인한 후 새로고침해 주세요.");
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
        const detail = typeof payload?.detail === "string"
            ? payload.detail
            : response.status === 422
                ? "입력값의 형식과 허용 범위를 확인해 주세요."
                : "서버 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
        const error = new Error(detail);
        error.status = response.status;
        throw error;
    }
    if (payload === null) throw new Error("서버 응답을 확인할 수 없습니다. 새로고침해 주세요.");
    return payload;
}

let dashboardRequestVersion = 0;

async function refreshDashboard() {
    const version = ++dashboardRequestVersion;
    const status = document.getElementById("dashboard-status");
    status.textContent = "실적을 불러오는 중입니다...";
    status.dataset.kind = "info";
    const year = document.getElementById("dashboard-year-select").value;
    const typeUrl = year === "all" ? "/api/data/project-type-summary"
        : `/api/data/project-type-summary?year=${encodeURIComponent(year)}`;
    try {
        const [yearly, monthly, types] = await Promise.all([
            readApi("/api/data/yearly-summary"), readApi("/api/data/monthly-summary"), readApi(typeUrl)
        ]);
        if (version !== dashboardRequestVersion) return false;
        const results = await Promise.all([
            loadDashboardSummary(yearly), loadYearlyChart(yearly), loadMonthlyChart(monthly),
            loadProjectTypeValueChart(types), loadProjectTypeCountChart(types)
        ]);
        if (version !== dashboardRequestVersion) return false;
        if (results.includes(false)) throw new Error("그래프를 표시하지 못했습니다. 새로고침해 주세요.");
        status.textContent = Object.keys(yearly).length ? "" : "등록된 수주 데이터가 없습니다. 데이터 관리에서 프로젝트를 추가해 주세요.";
        return true;
    } catch (error) {
        if (version !== dashboardRequestVersion) return false;
        status.textContent = error.message;
        status.dataset.kind = "error";
        for (const id of ["total-order-value", "total-order-count", "average-order-value"]) {
            document.getElementById(id).textContent = "-";
        }
        for (const chart of [yearlyChart, monthlyChart, projectTypeValueChart, projectTypeCountChart]) {
            if (chart) chart.destroy();
        }
        yearlyChart = monthlyChart = projectTypeValueChart = projectTypeCountChart = null;
        return false;
    }
}
