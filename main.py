import json
import os
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# =========================
# Environment variables
# =========================

# 채널톡 웹훅 토큰(채널톡 화면에서 생성되는 token 값)
CHANNELETALK_WEBHOOK_TOKEN = os.getenv("CHANNELETALK_WEBHOOK_TOKEN", "").strip()

# 팀ID(채널톡) -> 어떤 팀인지 매핑
TEAM_ID_TECH = os.getenv("TEAM_ID_TECH", "13366").strip()  # 바다코리아기술지원
TEAM_ID_CX = os.getenv("TEAM_ID_CX", "2704").strip()      # CX모아라인

# Slack Incoming Webhook URL (팀별로 따로 추천)
SLACK_WEBHOOK_URL_TECH = os.getenv("SLACK_WEBHOOK_URL_TECH", "").strip()
SLACK_WEBHOOK_URL_CX = os.getenv("SLACK_WEBHOOK_URL_CX", "").strip()
# (테스트용) 둘 다 비었을 때 fallback
SLACK_WEBHOOK_URL_DEFAULT = os.getenv("SLACK_WEBHOOK_URL", "").strip()

# 멘션(진짜 멘션하려면 Slack User ID로 넣는게 가장 확실: <@U123...>)
SLACK_MENTION_TECH = os.getenv("SLACK_MENTION_TECH", "@구교선").strip()
SLACK_MENTION_CX = os.getenv("SLACK_MENTION_CX", "@박서현").strip()

# "신규문의" 중복 방지 (같은 chatId에 대해 첫 알림 1회만)
DEDUP_TTL_SECONDS = int(os.getenv("DEDUP_TTL_SECONDS", "3600").strip() or "3600")
# 메모리 기반 dedup: { key: last_sent_epoch_seconds }
_SENT_CACHE: Dict[str, float] = {}


# =========================
# Helpers
# =========================

def _get(d: Dict[str, Any], path: str, default=None):
    cur: Any = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def extract_message_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    채널톡 webhook이 케이스에 따라
      - payload.entity 가 message일 수도 있고
      - payload.refers.message 안에 message가 있을 수도 있음
    '메시지 객체'를 통일해서 뽑아줌
    """
    if not isinstance(payload, dict):
        return None

    msg = _get(payload, "refers.message")
    if isinstance(msg, dict) and _get(msg, "personType"):
        return msg

    ent = _get(payload, "entity")
    if isinstance(ent, dict) and _get(ent, "personType"):
        return ent

    return None


def extract_userchat(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    userChat 객체는
      - payload.entity 가 userChat일 수도 있고
      - payload.refers.userChat 에 있을 수도 있음
    """
    ent = _get(payload, "entity")
    if isinstance(ent, dict) and ent.get("chatType") == "userChat":
        return ent

    uc = _get(payload, "refers.userChat")
    if isinstance(uc, dict):
        return uc

    return {}


def pick_slack_target(team_id: str) -> Tuple[str, str, str]:
    """
    return (webhook_url, mention, team_name)
    """
    if team_id == TEAM_ID_TECH:
        url = SLACK_WEBHOOK_URL_TECH or SLACK_WEBHOOK_URL_DEFAULT
        return url, SLACK_MENTION_TECH, "바다코리아기술지원"

    if team_id == TEAM_ID_CX:
        url = SLACK_WEBHOOK_URL_CX or SLACK_WEBHOOK_URL_DEFAULT
        return url, SLACK_MENTION_CX, "CX모아라인"

    url = SLACK_WEBHOOK_URL_DEFAULT or SLACK_WEBHOOK_URL_TECH or SLACK_WEBHOOK_URL_CX
    return url, "", f"Unknown(teamId={team_id})"


def is_new_inquiry(payload: Dict[str, Any]) -> bool:
    """
    '신규 문의(첫 유저 메시지)'만 True
    - 메시지 personType == "user"
    - userChat.firstAskedAt == message.createdAt
    """
    msg = extract_message_payload(payload)
    if not msg:
        return False

    if msg.get("personType") != "user":
        return False

    created_at = msg.get("createdAt")
    first_asked_at = _get(payload, "entity.firstAskedAt") or _get(payload, "refers.userChat.firstAskedAt")

    return created_at is not None and first_asked_at is not None and created_at == first_asked_at


def _now_epoch() -> float:
    import time
    return time.time()


def dedup_should_send(key: str) -> bool:
    """
    같은 key에 대해 TTL 내에는 1회만 보내도록.
    Render 단일 인스턴스 기준 메모리 dedup.
    """
    now = _now_epoch()

    # 청소
    if _SENT_CACHE:
        expired = [k for k, t in _SENT_CACHE.items() if now - t > DEDUP_TTL_SECONDS]
        for k in expired:
            _SENT_CACHE.pop(k, None)

    last = _SENT_CACHE.get(key)
    if last is not None and (now - last) <= DEDUP_TTL_SECONDS:
        return False

    _SENT_CACHE[key] = now
    return True


def post_to_slack(webhook_url: str, text: str) -> None:
    print(f"SLACK: target_url_set={bool(webhook_url)} text_len={len(text)}")

    if not webhook_url:
        print("SLACK: webhook url is empty. skip sending.")
        return

    try:
        r = requests.post(webhook_url, json={"text": text}, timeout=8)
        print(f"SLACK: status={r.status_code} resp={r.text[:300]}")
        if r.status_code >= 300:
            print(f"SLACK: failed body={r.text[:1000]}")
    except Exception as e:
        print(f"SLACK: exception {e}")


# =========================
# Routes
# =========================

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
    if CHANNELETALK_WEBHOOK_TOKEN and token != CHANNELETALK_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 2) payload 받기
    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw": (await request.body()).decode("utf-8", errors="ignore")}

    # 3) 원본 로그 (필요하면 길이 조절)
    print("CHANNELETALK_WEBHOOK:", json.dumps(payload, ensure_ascii=False)[:8000])

    if not isinstance(payload, dict):
        return JSONResponse({"received": True})

    # 4) 신규 문의만 Slack으로 알림
    if is_new_inquiry(payload):
        msg = extract_message_payload(payload) or {}
        userchat = extract_userchat(payload)

        team_id = str(userchat.get("teamId", "") or "")
        chat_id = str(msg.get("chatId", "") or userchat.get("id", "") or "")
        channel_id = str(msg.get("channelId", "") or userchat.get("channelId", "") or "")
        user_name = str(_get(payload, "refers.user.name", "") or userchat.get("name", "") or "")
        text = str(msg.get("plainText", "") or "")

        # dedup key: teamId + chatId (같은 문의로 여러 webhook push 와도 1회만)
        dedup_key = f"{team_id}:{chat_id}:new_inquiry"
        if dedup_should_send(dedup_key):
            webhook_url, mention, team_name = pick_slack_target(team_id)

            slack_text = (
                f"📩 채널톡 신규 문의 ({team_name})\n"
                f"{mention}\n"
                f"- channelId: {channel_id}\n"
                f"- teamId: {team_id}\n"
                f"- chatId: {chat_id}\n"
                f"- 고객: {user_name}\n"
                f"- 내용:\n{text}"
            ).strip()

            post_to_slack(webhook_url, slack_text)
        else:
            print(f"SLACK: dedup hit. skip sending. key={dedup_key}")

    return JSONResponse({"received": True})
