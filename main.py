import json
import os
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

# 채널톡 웹훅 토큰(채널톡 화면에서 생성되는 token 값)
CHANNELETALK_WEBHOOK_TOKEN = os.getenv("CHANNELETALK_WEBHOOK_TOKEN", "").strip()

# 팀ID(채널톡) -> 어떤 팀인지 매핑
TEAM_ID_TECH = os.getenv("TEAM_ID_TECH", "13366").strip()  # 바다코리아기술지원
TEAM_ID_CX = os.getenv("TEAM_ID_CX", "2704").strip()       # CX모아라인

# Slack Incoming Webhook URL (팀별로 따로 추천)
SLACK_WEBHOOK_URL_TECH = os.getenv("SLACK_WEBHOOK_URL_TECH", "").strip()
SLACK_WEBHOOK_URL_CX = os.getenv("SLACK_WEBHOOK_URL_CX", "").strip()
# (테스트용) 둘 다 비었을 때 fallback
SLACK_WEBHOOK_URL_DEFAULT = os.getenv("SLACK_WEBHOOK_URL", "").strip()

# 멘션(진짜 태그하려면 Slack User ID를 넣어야 함: <@U123...>)
# 없으면 그냥 "@이름" 텍스트로라도 표시되게 fallback 처리
SLACK_MENTION_TECH = os.getenv("SLACK_MENTION_TECH", "@구교선").strip()
SLACK_MENTION_CX = os.getenv("SLACK_MENTION_CX", "@박서현").strip()


def _get(d: Dict[str, Any], path: str, default=None):
    cur: Any = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def pick_slack_target(team_id: str) -> (str, str, str):
    """
    return (webhook_url, mention, team_name)
    """
    if team_id == TEAM_ID_TECH:
        url = SLACK_WEBHOOK_URL_TECH or SLACK_WEBHOOK_URL_DEFAULT
        return url, SLACK_MENTION_TECH, "바다코리아기술지원"
    if team_id == TEAM_ID_CX:
        url = SLACK_WEBHOOK_URL_CX or SLACK_WEBHOOK_URL_DEFAULT
        return url, SLACK_MENTION_CX, "CX모아라인"

    # 알 수 없는 teamId는 default로
    url = SLACK_WEBHOOK_URL_DEFAULT or SLACK_WEBHOOK_URL_TECH or SLACK_WEBHOOK_URL_CX
    return url, "", f"Unknown(teamId={team_id})"


def is_new_inquiry(payload: Dict[str, Any]) -> bool:
    """
    '문의가 새로 들어온 순간(첫 유저 메시지)'만 True
    조건:
      - entity.personType == "user"
      - refers.userChat.firstAskedAt == entity.createdAt
    """
    person_type = _get(payload, "entity.personType", "")
    if person_type != "user":
        return False

    created_at = _get(payload, "entity.createdAt")
    first_asked_at = _get(payload, "refers.userChat.firstAskedAt")

    return created_at is not None and first_asked_at is not None and created_at == first_asked_at


def post_to_slack(webhook_url: str, text: str) -> None:
    if not webhook_url:
        # URL이 없으면 조용히 스킵(테스트 중엔 로그로만 확인 가능)
        print("SLACK: webhook url is empty. skip sending.")
        return

    try:
        r = requests.post(webhook_url, json={"text": text}, timeout=5)
        if r.status_code >= 300:
            print(f"SLACK: failed status={r.status_code}, body={r.text[:1000]}")
    except Exception as e:
        print(f"SLACK: exception {e}")


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

    # 3) 로그 (원하면 일부만)
    print("CHANNELETALK_WEBHOOK:", json.dumps(payload, ensure_ascii=False)[:8000])

    # 4) '신규 문의(첫 유저 메시지)'일 때만 Slack으로 알림
    if isinstance(payload, dict) and is_new_inquiry(payload):
        team_id = str(_get(payload, "refers.userChat.teamId", "") or "")
        chat_id = str(_get(payload, "entity.chatId", "") or "")
        channel_id = str(_get(payload, "entity.channelId", "") or "")
        user_name = str(_get(payload, "refers.user.name", "") or _get(payload, "refers.userChat.name", "") or "")
        text = str(_get(payload, "entity.plainText", "") or "")

        webhook_url, mention, team_name = pick_slack_target(team_id)

        # 보기 좋게 포맷
        msg = (
            f"📩 채널톡 신규 문의 ({team_name})\n"
            f"{mention}\n"
            f"- channelId: {channel_id}\n"
            f"- teamId: {team_id}\n"
            f"- chatId: {chat_id}\n"
            f"- 고객: {user_name}\n"
            f"- 내용:\n{text}"
        ).strip()

        post_to_slack(webhook_url, msg)

    return JSONResponse({"received": True})
