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

# 預設展示資料庫（當雲端 DB 尚未連線或未建表時自動啟用，確保後台與 LINE 100% 正常秒回）
DEFAULT_BOOKINGS = [
    {
        "building_name": "A棟 現代館",
        "room_no": "101",
        "room_type": "頂級海景雙人房",
        "guest_name": "陳冠宇",
        "phone": "0912345678",
        "stay_count": 3,
        "total_spent": 38500,
        "extra_beds": 1,
        "breakfast_mode": "一人一份",
        "breakfast_time": "08:00",
        "breakfast_portions": 3,
        "breakfast_note": "如因趕行程（如看日出、搭船）需早出門調整時間，請前晚22:00前告知管家！",
        "bbq_vendor": "阿輝炭火烤肉 (海陸B餐)",
        "bbq_time": "18:30 開火",
        "bbq_status": "已叫料確認",
        "tour_vendor": "藍海俱樂部 SUP (3人)",
        "tour_details": "明日 09:30 接駁",
        "tour_status": "教練已排班",
        "wifi_pass": "ocean101",
        "car_plate": "ABC-5678",
        "crm_tags": ["VIP 尊榮常客", "海景偏好"],
        "line_user_id": None,
        "hotel_name": "海島晴天渡假會館"
    },
    {
        "building_name": "B棟 森林館",
        "room_no": "202",
        "room_type": "森林微風雙人房",
        "guest_name": "林雅婷",
        "phone": "0988765432",
        "stay_count": 1,
        "total_spent": 6200,
        "extra_beds": 0,
        "breakfast_mode": "自助Bar",
        "breakfast_time": "07:30~09:00",
        "breakfast_portions": 2,
        "breakfast_note": "一樓陽光玻璃屋供應，請於時段內自由入座。",
        "bbq_vendor": "",
        "bbq_time": "",
        "bbq_status": "無預訂",
        "tour_vendor": "",
        "tour_details": "",
        "tour_status": "無預訂",
        "wifi_pass": "forest202",
        "car_plate": "未登記",
        "crm_tags": ["首訪新客"],
        "line_user_id": None,
        "hotel_name": "海島晴天渡假會館"
    },
    {
        "building_name": "C棟 尊榮包棟",
        "room_no": "VIP包棟",
        "room_type": "全棟4間套房 (共12人)",
        "guest_name": "高橋 涼介",
        "phone": "0988123456",
        "stay_count": 5,
        "total_spent": 88000,
        "extra_beds": 2,
        "breakfast_mode": "一人一份",
        "breakfast_time": "09:00",
        "breakfast_portions": 12,
        "breakfast_note": "客房專屬派送。如需配合早起包船行程可提早於06:30外帶餐盒。",
        "bbq_vendor": "星空私廚代烤 (12人份)",
        "bbq_time": "18:00 泳池畔",
        "bbq_status": "私廚已就位",
        "tour_vendor": "私人遊艇夕陽巡航 (包船)",
        "tour_details": "明日 15:30 專車接送",
        "tour_status": "遊艇已鎖定",
        "wifi_pass": "vip888",
        "car_plate": "RZZ-8888",
        "crm_tags": ["黑卡包棟", "高消費客群"],
        "line_user_id": None,
        "hotel_name": "海島晴天渡假會館"
    }
]

def get_db():
    if not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"資料庫連線失敗: {e}")
        return None

def init_db():
    conn = get_db()
    if not conn:
        print("使用內建記憶體資料庫模式運行。")
        return

    try:
        with conn:
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
                    for d in DEFAULT_BOOKINGS:
                        cur.execute("""
                        INSERT INTO bookings (
                            hotel_id, building_name, room_no, room_type, guest_name, phone, stay_count, total_spent,
                            extra_beds, breakfast_mode, breakfast_time, breakfast_portions, breakfast_note,
                            bbq_vendor, bbq_time, bbq_status, tour_vendor, tour_details, tour_status,
                            wifi_pass, car_plate, crm_tags
                        ) VALUES 
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING;
                        """, (
                            hid, d["building_name"], d["room_no"], d["room_type"], d["guest_name"], d["phone"],
                            d["stay_count"], d["total_spent"], d["extra_beds"], d["breakfast_mode"], d["breakfast_time"],
                            d["breakfast_portions"], d["breakfast_note"], d["bbq_vendor"], d["bbq_time"], d["bbq_status"],
                            d["tour_vendor"], d["tour_details"], d["tour_status"], d["wifi_pass"], d["car_plate"], d["crm_tags"]
                        ))
        print("✅ PostgreSQL 資料表初始化完成！")
    except Exception as e:
        print(f"資料表建立異常，自動降級運行: {e}")

@app.on_event("startup")
def startup_event():
    init_db()

# --- 後台管理頁面 (/admin) ---
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return """
