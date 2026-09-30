import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Header, status
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

load_dotenv()
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app = FastAPI(title="PMS Multi-tenant Operations System")

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL:
        print("未偵測到 DATABASE_URL，略過建表")
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS hotels (
                id SERIAL PRIMARY KEY,
                hotel_code VARCHAR(50) UNIQUE NOT NULL,
                hotel_name VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY,
                hotel_id INT REFERENCES hotels(id) ON DELETE CASCADE,
                building_name VARCHAR(50) NOT NULL DEFAULT 'A棟 現代館',
                room_no VARCHAR(20) NOT NULL,
                room_type VARCHAR(100) NOT NULL,
                guest_name VARCHAR(50) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                stay_count INT DEFAULT 1,
                total_spent INT DEFAULT 0,
                extra_beds INT DEFAULT 0,
                breakfast_mode VARCHAR(50) DEFAULT '一人一份',
                breakfast_time VARCHAR(50) DEFAULT '08:00',
                breakfast_portions INT DEFAULT 2,
                breakfast_note VARCHAR(255) DEFAULT '如因趕行程需早起出門調整取餐時間，請於前晚22:00前告知管家為您準備！',
                bbq_vendor VARCHAR(100) DEFAULT '',
                bbq_time VARCHAR(50) DEFAULT '',
                bbq_status VARCHAR(50) DEFAULT '無預訂',
                tour_vendor VARCHAR(100) DEFAULT '',
                tour_details VARCHAR(200) DEFAULT '',
                tour_status VARCHAR(50) DEFAULT '無預訂',
                wifi_pass VARCHAR(50) DEFAULT 'ocean888',
                car_plate VARCHAR(20) DEFAULT '',
                crm_tags TEXT[] DEFAULT ARRAY[]::TEXT[],
                line_user_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (hotel_id, room_no)
            );
            """)

            cur.execute("SELECT COUNT(*) FROM hotels;")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO hotels (hotel_code, hotel_name) VALUES ('ocean_villa', '海島晴天渡假會館') RETURNING id;")
                hid = cur.fetchone()[0]

                cur.execute("""
                INSERT INTO bookings (
                    hotel_id, building_name, room_no, room_type, guest_name, phone, stay_count, total_spent,
                    extra_beds, breakfast_mode, breakfast_time, breakfast_portions, breakfast_note,
                    bbq_vendor, bbq_time, bbq_status, tour_vendor, tour_details, tour_status,
                    wifi_pass, car_plate, crm_tags
                ) VALUES 
                (%s, 'A棟 現代館', '101', '頂級海景雙人房', '陳冠宇', '0912345678', 3, 38500, 1, '一人一份', '08:00', 3, '如因趕行程（如看日出、搭船）需早出門調整時間，請前晚22:00前告知管家！', '阿輝炭火烤肉 (海陸B餐)', '18:30 開火', '已叫料確認', '藍海俱樂部 SUP (3人)', '明日 09:30 接駁', '教練已排班', 'ocean101', 'ABC-5678', ARRAY['VIP 尊榮常客', '海景偏好']),
                (%s, 'B棟 森林館', '202', '森林微風雙人房', '林雅婷', '0988765432', 1, 6200, 0, '自助Bar', '07:30~09:00', 2, '一樓陽光玻璃屋供應，請於時段內自由入座。', '', '', '無預訂', '', '', '無預訂', 'forest202', '未登記', ARRAY['首訪新客']),
                (%s, 'C棟 尊榮包棟', 'VIP包棟', '全棟4間套房', '高橋 涼介', '0988123456', 5, 88000, 2, '一人一份', '09:00', 12, '客房專屬派送。如需配合早起包船行程可提早於06:30外帶餐盒。', '星空私廚代烤 (12人份)', '18:00 泳池畔', '私廚已就位', '私人遊艇夕陽巡航 (包船)', '明日 15:30 專車接送', '遊艇已鎖定', 'vip888', 'RZZ-8888', ARRAY['黑卡包棟', '高消費客群']);
                """, (hid, hid, hid))
        conn.commit()
    print("✅ 資料庫初始化與 3 種早餐模式升級完成！")

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return """<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>旅宿智慧管家 - 每日排房與廠商派工總表</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen">
  <header class="bg-slate-900 text-white sticky top-0 z-30 shadow-md">
    <div class="max-w-7xl mx-auto px-4 py-3 flex justify-between items-center">
      <div class="flex items-center space-x-3">
        <div class="bg-sky-500 text-white p-2 rounded-lg"><i class="fa-solid fa-hotel text-lg"></i></div>
        <div>
          <h1 class="text-lg font-bold">海島晴天渡假會館 PMS</h1>
          <p class="text-xs text-sky-400">每日入住排房・早餐三模式・協力廠商派工管理</p>
        </div>
      </div>
      <div class="flex items-center space-x-2">
        <button onclick="exportVendorSheet()" class="bg-orange-600 hover:bg-orange-500 text-white px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 shadow transition">
          <i class="fa-solid fa-share-nodes"></i> 產出廠商派工通知
        </button>
        <button onclick="location.reload()" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5">
          <i class="fa-solid fa-rotate-right"></i> 刷新資料
        </button>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 py-6">
    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 mb-6 flex flex-wrap justify-between items-center gap-4">
      <div class="flex items-center space-x-1 bg-slate-100 p-1 rounded-lg" id="filterTabs">
        <button onclick="filterBuilding('all')" class="px-4 py-1.5 rounded-md text-sm font-semibold bg-white text-slate-900 shadow-sm tab-btn" data-target="all">全部三棟</button>
        <button onclick="filterBuilding('A棟')" class="px-4 py-1.5 rounded-md text-sm font-medium text-slate-600 tab-btn" data-target="A棟">A棟 現代館</button>
        <button onclick="filterBuilding('B棟')" class="px-4 py-1.5 rounded-md text-sm font-medium text-slate-600 tab-btn" data-target="B棟">B棟 森林館</button>
        <button onclick="filterBuilding('C棟')" class="px-4 py-1.5 rounded-md text-sm font-medium text-slate-600 tab-btn" data-target="C棟">C棟 尊榮包棟</button>
      </div>
      <div class="text-xs text-slate-500">
        🟢 實時同步 LINE 官方帳號資料庫
      </div>
    </div>

    <div id="bookingCards" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      <p class="text-slate-400 text-sm">載入資料中...</p>
    </div>
  </main>

  <script>
    let allData = [];
    async function loadData() {
      const res = await fetch('/api/admin/bookings');
      allData = await res.json();
      renderCards(allData);
    }

    function renderCards(list) {
      const container = document.getElementById('bookingCards');
      container.innerHTML = '';
      list.forEach(item => {
        const hasBBQ = item.bbq_vendor && item.bbq_vendor.trim() !== '';
        const hasTour = item.tour_vendor && item.tour_vendor.trim() !== '';
        const isBound = item.line_user_id ? true : false;
        
        let bText = "";
        if (item.breakfast_mode === "不提供") {
          bText = `<span class="text-slate-400">不提供早餐</span>`;
        } else if (item.breakfast_mode === "自助Bar") {
          bText = `<span class="font-bold text-amber-700">自助 Bar</span> (07:30~09:00)`;
        } else {
          bText = `<span class="font-bold text-sky-700">一人一份</span> 時段: ${item.breakfast_time} (${item.breakfast_portions}份)`;
        }

        const card = document.createElement('div');
        card.className = "bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden";
        card.innerHTML = `
          <div class="bg-slate-900 text-white px-4 py-2.5 flex justify-between items-center">
            <div class="flex items-center space-x-2">
              <span class="bg-sky-500 text-white text-xs font-bold px-2 py-0.5 rounded">${item.building_name.split(' ')[0]}</span>
              <span class="text-base font-bold">${item.room_no}</span>
              <span class="text-xs text-slate-300">${item.room_type}</span>
            </div>
            <span class="text-xs px-2 py-0.5 rounded-full ${isBound ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-700 text-slate-400'}">
              ${isBound ? '● LINE已綁定' : '未綁定'}
            </span>
          </div>
          <div class="p-4 space-y-3 text-sm">
            <div class="flex justify-between items-start border-b border-slate-100 pb-2.5">
              <div>
                <div class="font-bold text-slate-900 text-base">${item.guest_name} 貴賓</div>
                <div class="text-xs text-slate-500"><i class="fa-solid fa-phone mr-1"></i>${item.phone}</div>
              </div>
              <span class="text-xs font-semibold px-2 py-0.5 rounded ${item.extra_beds > 0 ? 'bg-amber-50 text-amber-700 border border-amber-200' : 'bg-slate-100 text-slate-500'}">
                加床: +${item.extra_beds}
              </span>
            </div>

            <div class="bg-amber-50/50 p-2.5 rounded-lg border border-amber-200/60">
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-bold text-amber-800"><i class="fa-solid fa-utensils mr-1"></i>早餐安排</span>
                <span class="text-xs">${bText}</span>
              </div>
              <div class="text-[11px] text-amber-900/80 bg-white/80 p-1.5 rounded border border-amber-100 mt-1">
                <i class="fa-regular fa-lightbulb text-amber-500 mr-1"></i>${item.breakfast_note}
              </div>
            </div>

            <div class="space-y-1.5">
              <div class="flex items-center justify-between text-xs p-2 rounded-lg ${hasBBQ ? 'bg-orange-50 text-orange-900 border border-orange-200' : 'bg-slate-50 text-slate-400'}">
                <span><i class="fa-solid fa-fire mr-1 text-orange-500"></i>烤肉：${hasBBQ ? item.bbq_vendor : '無預訂'}</span>
                <span class="font-bold">${hasBBQ ? item.bbq_status : ''}</span>
              </div>
              <div class="flex items-center justify-between text-xs p-2 rounded-lg ${hasTour ? 'bg-teal-50 text-teal-900 border border-teal-200' : 'bg-slate-50 text-slate-400'}">
                <span><i class="fa-solid fa-water mr-1 text-teal-500"></i>行程：${hasTour ? item.tour_vendor : '無預訂'}</span>
                <span class="font-bold">${hasTour ? item.tour_status : ''}</span>
              </div>
            </div>

            <div class="pt-2 flex justify-between items-center text-xs text-slate-400 border-t border-slate-100">
              <span>車牌：${item.car_plate || '未登記'}</span>
              <span>WiFi：${item.wifi_pass}</span>
            </div>
          </div>
        `;
        container.appendChild(card);
      });
    }

    function filterBuilding(bldg) {
      document.querySelectorAll('.tab-btn').forEach(btn => {
        if (btn.getAttribute('data-target') === bldg) {
          btn.className = "px-4 py-1.5 rounded-md text-sm font-semibold bg-white text-slate-900 shadow-sm tab-btn";
        } else {
          btn.className = "px-4 py-1.5 rounded-md text-sm font-medium text-slate-600 tab-btn";
        }
      });
      if (bldg === 'all') {
        renderCards(allData);
      } else {
        renderCards(allData.filter(d => d.building_name.includes(bldg)));
      }
    }

    function exportVendorSheet() {
      let text = "【海島晴天渡假會館 - 今日協力廠商派工清單】\n\n";
      text += "🥩 烤肉叫料明細：\n";
      allData.filter(d => d.bbq_vendor && d.bbq_vendor.trim() !== '').forEach(d => {
        text += `- 房號 ${d.room_no} (${d.guest_name}): ${d.bbq_vendor} / 時段: ${d.bbq_time}\n`;
      });
      text += "\n🏄 行程活動明細：\n";
      allData.filter(d => d.tour_vendor && d.tour_vendor.trim() !== '').forEach(d => {
        text += `- 房號 ${d.room_no} (${d.guest_name}): ${d.tour_vendor} / 細節: ${d.tour_details}\n`;
      });
      navigator.clipboard.writeText(text);
      alert("已複製「廠商派工通知」至剪貼簿！可直接貼到廠商 LINE 群組。");
    }

    loadData();
  </script>
</body>
</html>"""

@app.get("/api/admin/bookings")
def get_admin_bookings():
    if not DATABASE_URL:
        return []
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings ORDER BY building_name, room_no;")
            return cur.fetchall()

def query_guest_by_line_id(line_id: str):
    if not DATABASE_URL:
        return None
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
            SELECT b.*, h.hotel_name FROM bookings b
            JOIN hotels h ON b.hotel_id = h.id
            WHERE b.line_user_id = %s LIMIT 1;
            """, (line_id,))
            return cur.fetchone()

def bind_guest_by_phone(phone: str, line_id: str):
    if not DATABASE_URL:
        return None
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
            UPDATE bookings 
            SET line_user_id = %s 
            WHERE phone = %s 
            RETURNING *, (SELECT hotel_name FROM hotels WHERE id = bookings.hotel_id) AS hotel_name;
            """, (line_id, phone))
            guest = cur.fetchone()
        conn.commit()
        return guest

def build_stay_flex(guest: dict):
    tags_str = "、".join(guest.get("crm_tags") or ["尊榮貴賓"])
    has_bbq = guest.get("bbq_vendor") and guest.get("bbq_vendor").strip() != ""
    has_tour = guest.get("tour_vendor") and guest.get("tour_vendor").strip() != ""

    b_mode = guest.get("breakfast_mode", "一人一份")
    if b_mode == "不提供":
        b_content = "本專案未含早餐"
    elif b_mode == "自助Bar":
        b_content = "活力自助早餐 Bar (07:30~09:00)"
    else:
        b_content = f"一人一份套餐 ({guest['breakfast_portions']}份)\n領取時段：{guest['breakfast_time']}"

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#0F172A",
            "contents": [
                {"type": "text", "text": guest.get("hotel_name", "HOTEL PMS"), "color": "#38BDF8", "size": "xxs", "weight": "bold"},
                {"type": "text", "text": f"{guest['building_name']} - {guest['room_no']}（{guest['guest_name']} 貴賓）", "color": "#FFFFFF", "size": "sm", "weight": "bold", "margin": "xs"}
            ]
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "🍳 早餐安排", "size": "xs", "color": "#64748B", "flex": 3},
                        {"type": "text", "text": b_content, "size": "xs", "color": "#0F172A", "flex": 7, "wrap": True, "weight": "bold"}
                    ]
                },
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "💡 善意小提醒", "size": "xxs", "color": "#D97706", "flex": 3},
                        {"type": "text", "text": guest.get("breakfast_note", ""), "size": "xxs", "color": "#475569", "flex": 7, "wrap": True}
                    ]
                },
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "📶 客房網路", "size": "xs", "color": "#64748B", "flex": 3},
                        {"type": "text", "text": f"密碼: {guest['wifi_pass']}", "size": "xs", "color": "#0F172A", "flex": 7}
                    ]
                },
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "🥩 烤肉安排", "size": "xs", "color": "#64748B", "flex": 3},
                        {"type": "text", "text": f"{guest['bbq_vendor']} ({guest['bbq_status']})" if has_bbq else "無預訂", "size": "xs", "color": "#EA580C" if has_bbq else "#94A3B8", "flex": 7, "wrap": True}
                    ]
                },
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "🏄 活動行程", "size": "xs", "color": "#64748B", "flex": 3},
                        {"type": "text", "text": f"{guest['tour_vendor']}\n{guest['tour_details']}" if has_tour else "無預訂", "size": "xs", "color": "#0D9488" if has_tour else "#94A3B8", "flex": 7, "wrap": True}
                    ]
                },
                {"type": "separator", "margin": "sm"},
                {"type": "text", "text": f"🏷 偏好標籤：{tags_str}", "size": "xxs", "color": "#94A3B8", "wrap": True}
            ]
        }
    }
    return FlexMessage(alt_text=f"{guest['guest_name']} 貴賓專屬入住卡片", contents=FlexContainer.from_dict(bubble))

@app.post("/webhook")
async def line_webhook(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Signature")

    body = await request.body()
    body_str = body.decode("utf-8")
    handler = WebhookHandler(CHANNEL_SECRET if CHANNEL_SECRET else "temp")

    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event):
        uid = event.source.user_id
        text = event.message.text.strip()
        conf = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

        with ApiClient(conf) as client:
            bot = MessagingApi(client)
            guest = query_guest_by_line_id(uid)

            if guest:
                if "早餐" in text:
                    b_mode = guest.get("breakfast_mode", "一人一份")
                    if b_mode == "不提供":
                        b_reply = "您預訂的專案未包含早餐服務。如需周邊在地老街早餐推薦，可直接詢問管家！"
                    elif b_mode == "自助Bar":
                        b_reply = f"明日早餐為【活力自助早餐 Bar】，供應時段為 07:30 ~ 09:00，請直接前往一樓景觀玻璃屋享用。"
                    else:
                        b_reply = f"為您安排【一人一份特製早餐】（共 {guest['breakfast_portions']} 份），領取時段為：{guest['breakfast_time']}。"

                    msg = (
                        f"【{guest['hotel_name']} 早餐服務】\n"
                        f"{guest['guest_name']} 貴賓您好！您入住【{guest['building_name']} - {guest['room_no']}】\n"
                        f"🍴 {b_reply}\n\n"
                        f"💡 管家溫馨提醒：\n{guest['breakfast_note']}"
                    )
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=msg)]))
                    return
                elif "烤肉" in text or "bbq" in text.lower():
                    bbq_info = f"您預約的【{guest['bbq_vendor']}】，時段為：{guest['bbq_time']}，目前狀態：【{guest['bbq_status']}】。" if guest['bbq_vendor'] else "您目前尚未預約烤肉服務，如需代訂請直接聯繫管家！"
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=f"【烤肉行程安排】\n{bbq_info}")]))
                    return
                elif "行程" in text or "活動" in text or "sup" in text.lower():
                    tour_info = f"您預約的行程為【{guest['tour_vendor']}】。\n細節：{guest['tour_details']}\n狀態：【{guest['tour_status']}】" if guest['tour_vendor'] else "您目前尚未安排戶外行程活動。"
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=f"【活動行程確認】\n{tour_info}")]))
                    return
                elif "wifi" in text.lower() or "密碼" in text:
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=f"【客房網路 WiFi】\n{guest['building_name']} {guest['room_no']}\n密碼為：{guest['wifi_pass']}")]))
                    return
                elif "查詢" in text or "卡片" in text or "房況" in text:
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[build_stay_flex(guest)]))
                    return

            clean_phone = re.sub(r"[^\d]", "", text)
            if len(clean_phone) == 10:
                matched_guest = bind_guest_by_phone(clean_phone, uid)
                if matched_guest:
                    bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[
                        TextMessage(text=f"✅ 身分認證成功！歡迎入住【{matched_guest['hotel_name']}】。"),
                        build_stay_flex(matched_guest)
                    ]))
                    return

            bot.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[
                TextMessage(text="您好！歡迎使用旅宿智慧管家。\n請回傳您的「訂房手機號碼」即可連動入住資訊！")
            ]))

    try:
        handler.handle(body_str, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    return PlainTextResponse("OK", status_code=status.HTTP_200_OK)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
