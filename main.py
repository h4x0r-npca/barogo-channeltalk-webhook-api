import json
import os
import time
from typing import Any, Dict, Tuple
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# =========================
# Environment variables
# =========================

CHANNELETALK_WEBHOOK_TOKEN = os.getenv("CHANNELETALK_WEBHOOK_TOKEN", "").strip()

TEAM_ID_TECH = os.getenv("TEAM_ID_TECH", "13366").strip()
TEAM_ID_CX = os.getenv("TEAM_ID_CX", "2704").strip()

SLACK_WEBHOOK_URL_TECH = os.getenv("SLACK_WEBHOOK_URL_TECH", "").strip()
SLACK_WEBHOOK_URL_CX = os.getenv("SLACK_WEBHOOK_URL_CX", "").strip()
SLACK_WEBHOOK_URL_DEFAULT = os.getenv("SLACK_WEBHOOK_URL", "").strip()

SLACK_MENTION_TECH = os.getenv("SLACK_MENTION_TECH", "").strip()
SLACK_MENTION_CX = os.getenv("SLACK_MENTION_CX", "").strip()

DESK_WORKSPACE = os.getenv("DESK_WORKSPACE", "moaline").strip()

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
    return url, "", "문의"


def dedup_should_send(key: str) -> bool:
    now = time.time()
    expired = [k for k, t in _SENT_CACHE.items() if now - t > DEDUP_TTL_SECONDS]
    for k in expired:
        _SENT_CACHE.pop(k, None)

    last = _SENT_CACHE.get(key)
    if last and (now - last) <= DEDUP_TTL_SECONDS:
        return False

    _SENT_CACHE[key] = now
    return True


def post_to_slack(webhook_url: str, text: str) -> None:
    if not webhook_url:
        print("SLACK: webhook url empty")
        return

    try:
        r = requests.post(webhook_url, json={"text": text}, timeout=8)
        print(f"SLACK: status={r.status_code}")
    except Exception as e:
        print(f"SLACK error: {e}")


def is_new_inquiry(payload: Dict[str, Any]) -> bool:
    entity = payload.get("entity", {})
    return (
        entity.get("state") == "opened"
        and entity.get("managed") is True
        and entity.get("openedAt") == entity.get("firstOpenedAt")
    )


def build_desk_url(workspace: str, user_name: str, chat_id: str) -> str:
    safe_name = quote(user_name or "", safe="")
    return f"https://desk.channel.io/{workspace}/user-chats/{safe_name}-{chat_id}"


# =========================
# Routes
# =========================

@app.post("/webhook")
async def channeltalk_webhook(request: Request):
    token = request.query_params.get("token", "")
    if CHANNELETALK_WEBHOOK_TOKEN and token != CHANNELETALK_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    payload = await request.json()
    print("CHANNELETALK_WEBHOOK:", json.dumps(payload, ensure_ascii=False)[:2000])

    if not is_new_inquiry(payload):
        return JSONResponse({"received": True})

    entity = payload.get("entity", {}) or {}
    msg = _get(payload, "refers.message", {}) or {}
    user = _get(payload, "refers.user", {}) or {}

    team_id = str(entity.get("teamId", "") or "")
    chat_id = str(entity.get("id", "") or "")
    user_name = str(entity.get("name", "") or user.get("name", "") or "고객")
    phone = (
        user.get("mobileNumber")
        or _get(payload, "refers.user.profile.mobileNumber")
        or "-"
    )
    text = str(msg.get("plainText", "") or "")

    dedup_key = f"{team_id}:{chat_id}"
    if not dedup_should_send(dedup_key):
        return JSONResponse({"received": True})

    webhook_url, mention, team_name = pick_slack_target(team_id)

    desk_url = build_desk_url(DESK_WORKSPACE, user_name, chat_id)
    desk_link = f"<{desk_url}|👉 채널톡에서 바로 열기>"

    slack_text = (
        f"📩 신규 문의 ({team_name})\n"
        f"{mention}\n\n"
        f"👤 고객: {user_name}\n"
        f"📞 휴대폰: {phone}\n\n"
        f"📝 문의내용:\n{text}\n\n"
        f"{desk_link}"
    ).strip()

    post_to_slack(webhook_url, slack_text)

    return JSONResponse({"received": True})
