"""
夢想家旅宿 (Traiwan PMS) - LINE@ 官方帳號通訊串接微服務
功能包含：
1. 房客資料庫自動綁定 (CRM Matching)：免人工手動改名貼標籤，自動關聯萬筆舊客與今日房號
2. 智慧反向查詢引擎 (Reverse Query Engine)：房客問早餐、WiFi、門鎖密碼時自動秒回
3. 二度行銷標籤庫 (Second-time Marketing Tags)：記錄房客 LINE ID 與消費偏好供未來推播
4. 雙向通訊 Gateway：PMS 網頁管家統一發送與 LINE Webhook 接收
"""

import os
import sys
import re
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# LINE Bot SDK v3
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    FollowEvent,
    UnfollowEvent,
    PostbackEvent
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    ShowLoadingAnimationRequest
)
import json
import urllib.request
import urllib.error

# 載入環境變數
load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "8a9f4c3b2e1d0f5e7c8a9b0d1e2f3a4b")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "your_line_channel_access_token_here")
PMS_WEBHOOK_URL = os.getenv("PMS_WEBHOOK_URL", "http://localhost:3000/api/line/incoming-message")

# 配置日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LineFastAPIService")

app = FastAPI(
    title="Traiwan PMS - LINE@ 智慧房客資料庫與反向查詢 Gateway",
    description="整合 PMS 萬筆舊客戶資料庫、今日入住房號自動連動與 LINE@ 自動反向查詢",
    version="1.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

line_configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
line_parser = WebhookParser(LINE_CHANNEL_SECRET)


# ==========================================
# 萬筆歷史客戶資料庫 (模擬 PMS 既有 CRM 資料庫)
# ==========================================

CUSTOMER_DATABASE: List[Dict[str, Any]] = [
    {
        "id": "cust-001",
        "name": "陳冠宇",
        "phone": "0912-345-678",
        "email": "guanyu.chen@example.com",
        "past_stays": 3,
        "total_spent": 38500,
        "vip": True,
        "tags": ["VIP貴賓", "高樓層喜好", "週年蜜月"],
        "marketing_tags": ["二度行銷-淡季特惠", "蜜月紀念推播"],
        "current_room": "501",
        "order_number": "TW-20260902-8821",
        "line_user_id": "U1122334455aabbcc"
    },
    {
        "id": "cust-002",
        "name": "林雅婷",
        "phone": "0988-765-432",
        "email": "yating.lin@gmail.com",
        "past_stays": 2,
        "total_spent": 26800,
        "vip": False,
        "tags": ["家庭親子", "需嬰兒澡盆床", "愛吃在地早餐"],
        "marketing_tags": ["親子套裝優惠", "暑假家庭客"],
        "current_room": "201",
        "order_number": "TW-20260902-8904",
        "line_user_id": "U9988776655eeddcc"
    },
    {
        "id": "cust-003",
        "name": "張哲銘",
        "phone": "0933-112-233",
        "email": "ming.chang@techcorp.tw",
        "past_stays": 4,
        "total_spent": 42000,
        "vip": True,
        "tags": ["水上活動愛好者", "SUP教練團"],
        "marketing_tags": ["SUP水上新體驗", "秋季海島VIP專案"],
        "current_room": "302",
        "order_number": "TW-20260902-9901",
        "line_user_id": None
    },
    {
        "id": "cust-007",
        "name": "高橋 涼介",
        "phone": "0975-888-168",
        "email": "ryosuke.takahashi@gunma.jp",
        "past_stays": 3,
        "total_spent": 38400,
        "vip": True,
        "tags": ["VIP外籍常客", "自駕愛好者"],
        "marketing_tags": ["二度行銷-淡季包棟特惠"],
        "current_room": "501",
        "order_number": "TW-20260902-7712",
        "line_user_id": "U9a12bcde890f1234567890abcdef12"
    }
]

# LINE User ID 到客戶資料的即時快取索引
LINE_USER_BINDINGS: Dict[str, Dict[str, Any]] = {
    c["line_user_id"]: c for c in CUSTOMER_DATABASE if c.get("line_user_id")
}


# ==========================================
# 預設智慧反向查詢規則 (Reverse Query Engine)
# ==========================================

REVERSE_QUERY_RULES = [
    {
        "keywords": ["早餐", "吃早餐", "早餐時間", "陽光玻璃屋", "早點", "用餐", "幾點吃早餐"],
        "title": "手作早餐資訊",
        "template": (
            "【手作在地海島早餐】\n"
            "供應時間：每日 08:00 ~ 10:00\n"
            "用餐地點：一樓陽光玻璃屋\n"
            "{guest_info}\n"
            "若有素食、海鮮過敏或外帶需求，可直接回傳告訴管家！"
        )
    },
    {
        "keywords": ["wifi", "網路", "密碼", "wi-fi", "連線", "無線網路", "wifi密碼"],
        "title": "全館 WiFi 資訊",
        "template": (
            "【全館高速 WiFi 連線資訊】\n"
            "📡 基地台名稱：Traiwan_Guest\n"
            "🔑 連線密碼：staytraiwan2026\n"
            "客房床頭與各樓層均配置專屬 Mesh 訊號延伸器。"
        )
    },
    {
        "keywords": ["入住", "進房", "check in", "checkin", "幾點入住", "放行李", "密碼鎖", "門鎖"],
        "title": "入住與電子門鎖指引",
        "template": (
            "【入住時間與電子門鎖】\n"
            "入住時間：15:00 以後，退房時間：11:00 以前。\n"
            "大門與客房密碼鎖：【{door_code}】\n"
            "提早抵達可先至大廳免費寄放行李並享用迎賓冰飲！"
        )
    },
    {
        "keywords": ["接駁", "船票", "租車", "機車", "交通", "碼頭", "東港", "白沙港"],
        "title": "交通接駁指南",
        "template": (
            "【碼頭接駁與租車指南】\n"
            "抵達白沙碼頭後，特約電動機車「星海租車」（出碼頭右轉 50 公尺）會舉牌迎接您，並協助載運行李至民宿！"
        )
    }
]


# ==========================================
# 資料模型 (Pydantic Models)
# ==========================================

class HostReplyRequest(BaseModel):
    line_user_id: str = Field(..., description="LINE 房客 User ID")
    message: str = Field(..., description="管家回覆文字")
    host_name: Optional[str] = Field("星海民宿管家", description="管家稱謂")
    order_number: Optional[str] = Field(None, description="訂單編號")

class BindLineUserRequest(BaseModel):
    line_user_id: str
    phone: str
    line_display_name: Optional[str] = None


# ==========================================
# 核心端點 (Endpoints)
# ==========================================

@app.get("/")
async def root_status():
    return {
        "status": "online",
        "service": "Traiwan PMS - LINE@ 智慧房客資料庫與反向查詢 Gateway",
        "version": "1.2.0",
        "total_crm_records": len(CUSTOMER_DATABASE),
        "bound_line_users": len(LINE_USER_BINDINGS),
        "endpoints": {
            "webhook": "/api/line/webhook",
            "reply": "/api/line/send-message",
            "crm_lookup": "/api/crm/guest-lookup",
            "crm_bind": "/api/crm/bind-line-user",
            "marketing_list": "/api/crm/marketing-targets"
        }
    }


@app.get("/api/crm/guest-lookup")
async def lookup_guest(query: str = Query(..., description="手機號碼或姓名")):
    """從萬筆客戶資料庫與當日訂單反向搜尋房客"""
    clean_q = re.sub(r'[^0-9]', '', query)
    results = []

    for c in CUSTOMER_DATABASE:
        c_phone = re.sub(r'[^0-9]', '', c["phone"])
        if (clean_q and clean_q in c_phone) or query.lower() in c["name"].lower():
            results.append(c)

    return {"count": len(results), "guests": results}


@app.post("/api/crm/bind-line-user")
async def bind_line_user(payload: BindLineUserRequest):
    """
    一鍵將房客 LINE User ID 與舊客戶資料庫/今日訂單綁定
    徹底免去管家在 LINE@ 手動改名與貼標籤的苦工！
    """
    clean_p = re.sub(r'[^0-9]', '', payload.phone)
    matched_cust = None

    for c in CUSTOMER_DATABASE:
        if clean_p and clean_p in re.sub(r'[^0-9]', '', c["phone"]):
            matched_cust = c
            break

    if not matched_cust:
        matched_cust = {
            "id": f"cust-{len(CUSTOMER_DATABASE)+1:03d}",
            "name": payload.line_display_name or "新入住旅客",
            "phone": payload.phone,
            "past_stays": 1,
            "vip": False,
            "current_room": "501",
            "order_number": f"TW-{clean_p[-4:]}",
            "marketing_tags": ["二度行銷-新會員"]
        }
        CUSTOMER_DATABASE.append(matched_cust)

    matched_cust["line_user_id"] = payload.line_user_id
    matched_cust["line_display_name"] = payload.line_display_name
    LINE_USER_BINDINGS[payload.line_user_id] = matched_cust

    logger.info(f"成功綁定 LINE 房客 [{payload.line_user_id}] ➔ {matched_cust['name']} ({matched_cust['phone']}) 今日房號: {matched_cust.get('current_room')}")

    return {
        "success": True,
        "message": f"已成功綁定房客 {matched_cust['name']}，今日房號：{matched_cust.get('current_room')}",
        "customer": matched_cust
    }


@app.get("/api/crm/marketing-targets")
async def get_marketing_targets(tag: Optional[str] = Query(None, description="依二度行銷標籤篩選")):
    """二度行銷名單篩選：撈出已綁定 LINE@ 的過往房客，供淡季促銷或會員專案推播"""
    targets = []
    for c in CUSTOMER_DATABASE:
        if not c.get("line_user_id"):
            continue
        if tag:
            if tag in c.get("marketing_tags", []) or tag in c.get("tags", []):
                targets.append(c)
        else:
            targets.append(c)

    return {"count": len(targets), "targets": targets}


@app.post("/api/line/webhook")
async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: Optional[str] = Header(None, alias="X-Line-Signature")
):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    try:
        events = line_parser.parse(body_text, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    background_tasks.add_task(process_line_events, events)
    return {"status": "ok", "events_received": len(events)}


async def process_line_events(events: list):
    """處理 LINE 事件：支援自動反向查詢、手機號碼自動綁定與未命中訊息轉發 PMS"""
    async with ApiClient(line_configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        for event in events:
            if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                user_id = event.source.user_id
                guest_text = event.message.text.strip()
                reply_token = event.reply_token

                # 1. 檢查房客是否已綁定資料庫
                bound_customer = LINE_USER_BINDINGS.get(user_id)

                # 2. 自動綁定檢測：若房客輸入手機號碼 (如 0912-345-678 或 0912345678)
                phone_match = re.search(r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', guest_text)
                if phone_match and not bound_customer:
                    input_phone = phone_match.group(0)
                    for c in CUSTOMER_DATABASE:
                        if re.sub(r'[^0-9]', '', input_phone) in re.sub(r'[^0-9]', '', c["phone"]):
                            bound_customer = c
                            c["line_user_id"] = user_id
                            LINE_USER_BINDINGS[user_id] = c
                            break

                    if bound_customer:
                        room_no = bound_customer.get("current_room", "501")
                        welcome_reply = (
                            f"【身分已自動連動成功】\n"
                            f"{bound_customer['name']} 您好！已為您連動今日入住【{room_no} 號房】。\n"
                            f"您現在可以直接輸入「早餐」、「WiFi」、「密碼」查詢入住須知，免去人工等待！"
                        )
                        await messaging_api.reply_message(
                            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=welcome_reply)])
                        )
                        # 通知 PMS 介面
                        await forward_message_to_pms({
                            "line_user_id": user_id,
                            "guest_name": f"{bound_customer['name']} (LINE)",
                            "message": f"（房客已在 LINE 輸入手機 {input_phone} 完成身分綁定，今日入住房號：{room_no}）",
                            "order_number": bound_customer.get("order_number")
                        })
                        continue

                # 3. 智慧反向查詢匹配 (早餐 / WiFi / 密碼 / 接駁)
                clean_text = guest_text.lower()
                matched_rule = None
                for rule in REVERSE_QUERY_RULES:
                    if any(kw in clean_text for kw in rule["keywords"]):
                        matched_rule = rule
                        break

                if matched_rule:
                    guest_info = ""
                    door_code = "手機末四碼# 或 8899#"
                    if bound_customer:
                        room_no = bound_customer.get("current_room", "501")
                        guest_info = f"您今日入住【{room_no} 號房】，已為您登記用餐份數。"
                        door_code = f"{bound_customer['phone'][-4:]}# (您的手機末四碼)"

                    auto_reply_text = matched_rule["template"].format(
                        guest_info=guest_info,
                        door_code=door_code
                    )

                    await messaging_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=reply_token,
                            messages=[TextMessage(text=f"【管家智慧反向查詢】\n{auto_reply_text}")]
                        )
                    )

                    # 同步告知 PMS 系統房客剛剛自助查詢過此內容，管家無需重複敲字
                    await forward_message_to_pms({
                        "line_user_id": user_id,
                        "guest_name": f"{bound_customer['name']} (LINE)" if bound_customer else f"LINE 訪客 ({user_id[-4:]})",
                        "message": f"【反向查詢秒回】房客詢問「{guest_text}」➔ 系統已自動回傳 {matched_rule['title']}",
                        "order_number": bound_customer.get("order_number") if bound_customer else None
                    })
                    continue

                # 4. 未命中任何自動反向規則，交給真人管家在 PMS 網頁介面統一回覆
                guest_display_name = f"LINE 訪客 ({user_id[-4:]})"
                if bound_customer:
                    guest_display_name = f"{bound_customer['name']} (LINE)"
                else:
                    try:
                        profile = await messaging_api.get_profile(user_id)
                        if profile and profile.display_name:
                            guest_display_name = profile.display_name
                    except Exception:
                        pass

                await forward_message_to_pms({
                    "line_user_id": user_id,
                    "guest_name": guest_display_name,
                    "message": guest_text,
                    "order_number": bound_customer.get("order_number") if bound_customer else None
                })


async def forward_message_to_pms(payload: dict):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            PMS_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=2.0):
            pass
    except Exception:
        pass


@app.post("/api/line/send-message")
async def send_host_reply(payload: HostReplyRequest):
    """PMS 管家在網頁介面按下發送時，推播回傳至房客 LINE"""
    if not LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_ACCESS_TOKEN == "your_line_channel_access_token_here":
        return {
            "success": True,
            "mode": "simulation",
            "message": f"（模擬發送成功）已推播至房客 {payload.line_user_id}：{payload.message}"
        }

    try:
        async with ApiClient(line_configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            display_message = f"【管家回覆】\n{payload.message}"
            push_req = PushMessageRequest(
                to=payload.line_user_id,
                messages=[TextMessage(text=display_message)]
            )
            await messaging_api.push_message(push_req)
            return {"success": True, "delivered_to": payload.line_user_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
