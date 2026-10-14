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
    const question = userInput.value.trim();

    if (!question) return;

    addMessage("나", question);
    userInput.value = "";

    sendButton.disabled = true;
    sendButton.textContent = "답변 중...";

    try {
        const response = await fetch("http://127.0.0.1:8000/api/chat", {
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
            throw new Error("서버 응답 오류");
        }

        const data = await response.json();

        conversationId = data.conversation_id;
        addMessage("AI 비서", data.answer);

        // AI 답변 완료 후 이전 대화 목록 갱신
        loadConversations();

    } catch (error) {
        addMessage("시스템", "AI 서버에 연결할 수 없습니다.");
        console.error(error);

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
    if (event.key === "Enter") {
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

        // 현재 대화 ID 변경
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