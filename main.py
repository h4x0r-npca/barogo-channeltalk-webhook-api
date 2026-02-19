import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# =========================
# Environment variables
# =========================

CHANNELETALK_WEBHOOK_TOKEN = os.getenv("CHANNELETALK_WEBHOOK_TOKEN", "").strip()

TEAM_ID_TECH = os.getenv("TEAM_ID_TECH", "13366").strip()  # 바다코리아기술지원
TEAM_ID_CX = os.getenv("TEAM_ID_CX", "2704").strip()      # CX모아라인

SLACK_WEBHOOK_URL_TECH = os.getenv("SLACK_WEBHOOK_URL_TECH", "").strip()
SLACK_WEBHOOK_URL_CX = os.getenv("SLACK_WEBHOOK_URL_CX", "").strip()
SLACK_WEBHOOK_URL_DEFAULT = os.getenv("SLACK_WEBHOOK_URL", "").strip()

# 진짜 멘션하려면 <@Uxxxx> 형태 권장
SLACK_MENTION_TECH = os.getenv("SLACK_MENTION_TECH", "@구교선").strip()
SLACK_MENTION_CX = os.getenv("SLACK_MENTION_CX", "@박서현").strip()

# 중복 방지 (같은 문의가 여러번 push 와도 1회만)
DEDUP_TTL_SECONDS = int(os.getenv("DEDUP_TTL_SECONDS", "3600").strip() or "3600")
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


def pick_slack_target(team_id: str) -> Tuple[str, str, str]:
    if team_id == TEAM_ID_TECH:
        url = SLACK_WEBHOOK_URL_TECH or SLACK_WEBHOOK_URL_DEFAULT
        return url, SLACK_MENTION_TECH, "바다코리아기술지원"
    if team_id == TEAM_ID_CX:
        url = SLACK_WEBHOOK_URL_CX or SLACK_WEBHOOK_URL_DEFAULT
        return url, SLACK_MENTION_CX, "CX모아라인"
    url = SLACK_WEBHOOK_URL_DEFAULT or SLACK_WEBHOOK_URL_TECH or SLACK_WEBHOOK_URL_CX
    return url, "", f"Unknown(teamId={team_id})"


def dedup_should_send(key: str) -> bool:
    now = time.time()
    # cleanup
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


def is_new_inquiry_userchat_opened(payload: Dict[str, Any]) -> bool:
    """
    지금 네 로그처럼 entity가 userChat으로 오는 케이스에서,
    '신규 문의'를 userChat이 최초 opened 되는 순간으로 판정.
    """
    entity = _get(payload, "entity", {})
    if not isinstance(entity, dict):
        return False

    # userChat 객체는 chatType이 없을 수도 있어서 state로 판단
    state = entity.get("state")
    managed = entity.get("managed")
    opened_at = entity.get("openedAt")
    first_opened_at = entity.get("firstOpenedAt")

    # 최초 오픈 순간만 True
    return (
        state == "opened"
        and managed is True
        and opened_at is not None
        and first_opened_at is not None
        and opened_at == first_opened_at
    )


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
    token = request.query_params.get("token", "")
    if CHANNELETALK_WEBHOOK_TOKEN and token != CHANNELETALK_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw": (await request.body()).decode("utf-8", errors="ignore")}

    print("CHANNELETALK_WEBHOOK:", json.dumps(payload, ensure_ascii=False)[:8000])

    if not isinstance(payload, dict):
        return JSONResponse({"received": True})

    # ✅ 신규 문의 트리거: userChat 최초 opened
    if is_new_inquiry_userchat_opened(payload):
        entity = payload.get("entity", {}) or {}
        msg = _get(payload, "refers.message", {}) or {}

        team_id = str(entity.get("teamId", "") or "")
        chat_id = str(entity.get("id", "") or msg.get("chatId", "") or "")
        channel_id = str(entity.get("channelId", "") or "")
        user_name = str(entity.get("name", "") or _get(payload, "refers.user.name", "") or "")
        text = str(msg.get("plainText", "") or "")

        # dedup key: teamId + chatId
        dedup_key = f"{team_id}:{chat_id}:new_inquiry_opened"
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
