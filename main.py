import json
import os
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

# 채널톡 웹훅 토큰(채널톡 화면에서 생성되는 token 값) - Render 환경변수 권장
CHANNELETALK_WEBHOOK_TOKEN = os.getenv("CHANNELETALK_WEBHOOK_TOKEN", "").strip()

# Slack Incoming Webhook URL (Render 환경변수로 넣어야 안전)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()


@app.get("/")
def root():
    return {"ok": True, "message": "barogo channeltalk webhook api"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def send_to_slack(text: str):
    """슬랙으로 텍스트 메시지 전송"""
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL is empty. Skip sending to slack.")
        return

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": text},
            timeout=10,
        )
        if resp.status_code >= 400:
            print("Slack webhook failed:", resp.status_code, resp.text[:5000])
    except Exception as e:
        print("Slack webhook error:", repr(e))


@app.post("/webhook")
async def channeltalk_webhook(request: Request):
    # 1) 토큰 검증 (채널톡은 ?token=... 형태로 보냄)
    token = request.query_params.get("token", "")
    if CHANNELETALK_WEBHOOK_TOKEN and token != CHANNELETALK_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 2) payload 받기
    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw": (await request.body()).decode("utf-8", errors="ignore")}

    # 3) 로그로 payload 찍기 (처음엔 이게 제일 중요)
    print("CHANNELETALK_WEBHOOK:", json.dumps(payload, ensure_ascii=False)[:8000])

    # 4) "message" 이벤트 && 사용자 메시지일 때만 Slack으로 알림
    try:
        if payload.get("type") == "message":
            entity = payload.get("entity", {}) or {}
            person_type = entity.get("personType")  # user / manager / bot
            text = (entity.get("plainText") or "").strip()
            chat_id = entity.get("chatId", "")
            channel_id = entity.get("channelId", "")

            # 고객(user) 메시지만 알림 (상담원(manager) 메시지는 제외)
            if person_type == "user" and text:
                slack_text = (
                    f"📩 채널톡 새 문의\n"
                    f"- channelId: {channel_id}\n"
                    f"- chatId: {chat_id}\n"
                    f"- 내용: {text}"
                )
                send_to_slack(slack_text)
    except Exception as e:
        print("Failed to process payload for slack:", repr(e))

    # 5) 성공 응답
    return JSONResponse({"received": True})
