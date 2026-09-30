"""
夢想家旅宿 (Traiwan PMS) - LINE@ 官方帳號通訊串接微服務 v1.2.1
"""

import os
import sys
import re
import json
import logging
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

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

load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
PMS_WEBHOOK_URL = os.getenv("PMS_WEBHOOK_URL", "http://localhost:3000/api/line/incoming-message")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LineFastAPIService")

app = FastAPI(
    title="Traiwan PMS - LINE@ 智慧房客資料庫與反向查詢 Gateway",
    version="1.2.1"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

line_configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
line_parser = WebhookParser(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

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

LINE_USER_BINDINGS: Dict[str, Dict[str, Any]] = {
    c["line_user_id"]: c for c in CUSTOMER_DATABASE if c.get("line_user_id")
}

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

class HostReplyRequest(BaseModel):
    line_user_id: str
    message: str
    host_name: Optional[str] = "星海民宿管家"
    order_number: Optional[str] = None

class BindLineUserRequest(BaseModel):
    line_user_id: str
    phone: str
    line_display_name: Optional[str] = None

@app.get("/")
async def root_status():
    return {
        "status": "online",
        "service": "Traiwan PMS - LINE@ 智慧房客資料庫與反向查詢 Gateway",
        "version": "1.2.1",
        "channel_secret_configured": bool(LINE_CHANNEL_SECRET),
        "access_token_configured": bool(LINE_CHANNEL_ACCESS_TOKEN),
        "endpoints": {
            "webhook": "/api/line/webhook (也可使用 /webhook)",
            "reply": "/api/line/send-message",
            "crm_lookup": "/api/crm/guest-lookup",
            "crm_bind": "/api/crm/bind-line-user"
        }
    }

@app.post("/api/line/webhook")
@app.post("/webhook")
async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: Optional[str] = Header(None, alias="X-Line-Signature")
):
    if not line_parser:
        logger.error("尚未在 Render 設定 LINE_CHANNEL_SECRET 環境變數！")
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_SECRET missing")

    if not x_line_signature:
        logger.warning("收到請求但缺少 X-Line-Signature 標頭")
        raise HTTPException(status_code=400, detail="Missing signature header")

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    try:
        events = line_parser.parse(body_text, x_line_signature)
    except InvalidSignatureError:
        logger.error("LINE Signature 驗證失敗！請確認 Render 的 LINE_CHANNEL_SECRET 是否填對。")
        raise HTTPException(status_code=400, detail="Invalid signature")

    logger.info(f"收到 {len(events)} 個 LINE 事件，加入背景處理佇列")
    background_tasks.add_task(process_line_events, events)
    return {"status": "ok", "events_received": len(events)}

def process_line_events(events: list):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        logger.error("尚未在 Render 設定 LINE_CHANNEL_ACCESS_TOKEN 環境變數！無法回覆房客。")
        return

    try:
        with ApiClient(line_configuration) as api_client:
            messaging_api = MessagingApi(api_client)

            for event in events:
                if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                    user_id = event.source.user_id
                    guest_text = event.message.text.strip()
                    reply_token = event.reply_token
                    logger.info(f"收到來自房客 [{user_id}] 訊息: {guest_text}")

                    bound_customer = LINE_USER_BINDINGS.get(user_id)

                    # 電話自動綁定
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
                            messaging_api.reply_message(
                                ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=welcome_reply)])
                            )
                            logger.info(f"已回傳綁定成功訊息給房客 {user_id}")
                            continue

                    # 智慧反向查詢匹配
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

                        messaging_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=reply_token,
                                messages=[TextMessage(text=f"【管家智慧反向查詢】\n{auto_reply_text}")]
                            )
                        )
                        logger.info(f"已自動反向回覆 [{matched_rule['title']}] 給房客 {user_id}")
                        continue
    except Exception as e:
        logger.error(f"處理 LINE 事件時發生錯誤: {e}", exc_info=True)

@app.post("/api/line/send-message")
async def send_host_reply(payload: HostReplyRequest):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return {"success": True, "mode": "simulation"}

    try:
        with ApiClient(line_configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            display_message = f"【管家回覆】\n{payload.message}"
            push_req = PushMessageRequest(
                to=payload.line_user_id,
                messages=[TextMessage(text=display_message)]
            )
            messaging_api.push_message(push_req)
            return {"success": True, "delivered_to": payload.line_user_id}
    except Exception as e:
        logger.error(f"推播失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
