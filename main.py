import json
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

# 채널톡 웹훅 토큰(채널톡 화면에서 생성되는 token 값)
# Render 환경변수로 넣는 걸 추천. (Settings > Environment Variables)
CHANNELETALK_WEBHOOK_TOKEN = os.getenv("CHANNELETALK_WEBHOOK_TOKEN", "").strip()


@app.get("/")
def root():
    return {"ok": True, "message": "barogo channeltalk webhook api"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def channeltalk_webhook(request: Request):
    # 1) 토큰 검증 (채널톡은 ?token=... 형태로 보냄)
    token = request.query_params.get("token", "")
    if CHANNELETALK_WEBHOOK_TOKEN:
        if token != CHANNELETALK_WEBHOOK_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid token")

    # 2) payload 받기
    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw": (await request.body()).decode("utf-8", errors="ignore")}

    # 3) 로그로 payload 찍기 (처음엔 이게 제일 중요)
    print("CHANNELETALK_WEBHOOK:", json.dumps(payload, ensure_ascii=False)[:8000])

    # 4) 일단은 성공 응답
    return JSONResponse({"received": True})
