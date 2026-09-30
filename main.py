import os, re
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Header
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer
from linebot.v3.webhooks import MessageEvent, TextMessageContent

load_dotenv()
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()

app = FastAPI()

CRM_DATABASE = {
    "0988123456": {
        "name": "高橋 涼介", "stay_count": 5, "total_spent": 38400, "tags": ["高樓層海景偏好", "VIP 尊榮常客"],
        "room_no": "501", "room_type": "501 頂級海景套房", "breakfast_time": "08:00~10:00",
        "breakfast_location": "一樓陽光玻璃屋", "breakfast_portions": 2, "wifi_pass": "ocean501", "line_user_id": None
    },
    "0912345678": {
        "name": "陳冠宇", "stay_count": 3, "total_spent": 38500, "tags": ["淡季特惠名單", "週年蜜月", "VIP 尊榮常客"],
        "room_no": "501", "room_type": "501 頂級海景套房", "breakfast_time": "08:00~10:00",
        "breakfast_location": "一樓陽光玻璃屋", "breakfast_portions": 2, "wifi_pass": "ocean501", "line_user_id": None
    }
}

def get_guest_by_line_id(line_id: str):
    for phone, guest in CRM_DATABASE.items():
        if guest["line_user_id"] == line_id:
            return guest
    return None

def build_stay_flex(guest: dict):
    tags_str = "、".join(guest["tags"])
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#0F172A",
            "contents": [
                {"type": "text", "text": "HOTEL PMS GUEST SYSTEM", "color": "#38BDF8", "size": "xxs", "weight": "bold"},
                {"type": "text", "text": f"房號：{guest['room_no']}（{guest['name']} 貴賓）", "color": "#FFFFFF", "size": "md", "weight": "bold", "margin": "xs"}
            ]
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "🍳 早餐時段", "size": "xs", "color": "#64748B", "flex": 3}, {"type": "text", "text": f"{guest['breakfast_time']}\n{guest['breakfast_location']} ({guest['breakfast_portions']}份)", "size": "xs", "color": "#0F172A", "flex": 7, "wrap": True}]},
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "📶 客房網路", "size": "xs", "color": "#64748B", "flex": 3}, {"type": "text", "text": f"WiFi密碼: {guest['wifi_pass']}", "size": "xs", "color": "#0F172A", "flex": 7}]},
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "⭐ CRM 資料", "size": "xs", "color": "#64748B", "flex": 3}, {"type": "text", "text": f"累積入住 {guest['stay_count']} 次｜消費 NT${guest['total_spent']:,}", "size": "xs", "color": "#0369A1", "flex": 7, "weight": "bold"}]},
                {"type": "separator", "margin": "sm"},
                {"type": "text", "text": f"🏷️ 偏好：{tags_str}", "size": "xxs", "color": "#94A3B8", "wrap": True}
            ]
        }
    }
    return FlexMessage(alt_text="您的專屬入住資訊卡", contents=FlexContainer.from_dict(bubble))

@app.post("/webhook")
async def line_webhook(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing Signature")
    body = await request.body()
    body_str = body.decode("utf-8")
    handler = WebhookHandler(CHANNEL_SECRET)
    
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event):
        uid = event.source.user_id
        text = event.message.text.strip()
        conf = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
        with ApiClient(conf) as client:
            bot = MessagingApi(client)
            guest = get_guest_by_line_id(uid)
            if guest:
                if "早餐" in text:
                    msg = f"【管家智慧反向查詢】\n{guest['name']} 您好！您今日入住【{guest['room_type']}】，手作海島早餐於每日 {guest['breakfast_time']} 於{guest['breakfast_location']}供應，已為您登記 {guest['breakfast_portions']} 份。若有素食或過敏忌口請直接回傳告知管家！"
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=msg)]))
                    return
                elif "wifi" in text.lower() or "密碼" in text:
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=f"【客房網路密碼】\n房號：{guest['room_no']}\n密碼為：{guest['wifi_pass']}")]))
                    return
                elif "查詢" in text or "卡片" in text or "資訊" in text:
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[build_stay_flex(guest)]))
                    return

            clean_phone = re.sub(r"[^\d]", "", text)
            if len(clean_phone) == 10 and clean_phone in CRM_DATABASE:
                target = CRM_DATABASE[clean_phone]
                target["line_user_id"] = uid
                bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[
                    TextMessage(text=f"✅ 身分認證成功！歡迎 {target['name']} 貴賓，已為您連線 PMS 房務系統。"),
                    build_stay_flex(target)
                ]))
                return

            bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text="您好！歡迎加入旅宿管家。\n請直接回傳您的「訂房手機號碼」（例如：0912345678）即可連動入住資訊！")]))

    try:
        handler.handle(body_str, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"