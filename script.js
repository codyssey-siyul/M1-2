const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");

let conversationId = null;

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

    } catch (error) {
        addMessage("시스템", "AI 서버에 연결할 수 없습니다.");
        console.error(error);

    } finally {
        sendButton.disabled = false;
        sendButton.textContent = "전송";
        userInput.focus();
    }
}

// 전송 버튼 클릭
sendButton.addEventListener("click", sendMessage);

// Enter 키로 전송
userInput.addEventListener("keydown", function(event) {
    if (event.key === "Enter") {
        sendMessage();
    }
});