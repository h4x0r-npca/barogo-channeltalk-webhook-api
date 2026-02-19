from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/")
def root():
    return {"ok": True, "message": "barogo channeltalk webhook api"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook/channeltalk")
async def channeltalk_webhook(request: Request):
    """
    ChannelTalk에서 웹훅으로 POST 보내면 여기로 들어옴.
    일단은 payload를 그대로 반환(에코)해서 연결 테스트부터 함.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw": (await request.body()).decode("utf-8", errors="ignore")}

    # TODO: 여기서 payload 분석 → (라이더코드 추출/Redash 조회/자동응답)로 확장
    return JSONResponse({"received": True, "payload": payload})
