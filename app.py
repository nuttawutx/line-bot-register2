import os
import json
import base64
import re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv
from datetime import datetime
import pytz


load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GOOGLE_CREDENTIAL_BASE64 = os.getenv("GOOGLE_CREDENTIAL_BASE64")

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

cred_path = "google-credentials.json"
if GOOGLE_CREDENTIAL_BASE64:
    with open(cred_path, "w") as f:
        f.write(base64.b64decode(GOOGLE_CREDENTIAL_BASE64).decode("utf-8"))

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
client = gspread.authorize(creds)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

SYSTEM_ACTIVE = os.getenv("SYSTEM_ACTIVE", "true").lower() == "true"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    if not SYSTEM_ACTIVE:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⚠️ ขณะนี้ระบบลงทะเบียนปิดให้บริการชั่วคราว\nโปรดลองใหม่อีกครั้งภายหลัง")
        )
        return

    text = event.message.text
    user_id = event.source.user_id

    lines = text.strip().splitlines()
    if len(lines) != 6:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ต้องกรอกข้อมูล 6 บรรทัดเท่านั้น:\nชื่อ:\nชื่อเล่น:\nสาขา:\nตำแหน่ง:\nเริ่มงาน (DD-MM-YYYY):\nประเภท:")
        )
        return

    expected_keys = {"ชื่อ", "ชื่อเล่น", "สาขา", "ตำแหน่ง", "เริ่มงาน", "ประเภท"}
    data = {}
    for line in lines:
        if ":" not in line:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ ทุกบรรทัดต้องมีเครื่องหมาย ':' เช่น ตำแหน่ง: เจ้าหน้าที่")
            )
            return
        key, val = line.split(":", 1)
        data[key.strip()] = val.strip()

    if set(data.keys()) != expected_keys:
        missing = expected_keys - set(data.keys())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"❌ ขาดข้อมูล: {', '.join(missing)}")
        )
        return

    if not re.match(r'^\d{1,2}-\d{1,2}-\d{4}$', data["เริ่มงาน"]):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ รูปแบบวันเริ่มงานไม่ถูกต้อง (ต้องเป็น DD-MM-YYYY)")
        )
        return

    try:
        name = data.get("ชื่อ", "")
        nickname = data.get("ชื่อเล่น", "")
        branch = data.get("สาขา", "")
        postion = data.get("ตำแหน่ง", "")
        start = data.get("เริ่มงาน", "")
        emp_type = data.get("ประเภท", "").strip().lower()

        if emp_type == "รายวัน":
            worksheet = client.open("HR_EmployeeListMikka").worksheet("DailyEmployee")
            default_code = 20000
            prefix = ""
        elif emp_type == "รายเดือน":
            worksheet = client.open("HR_EmployeeListMikka").worksheet("MonthlyEmployee")
            default_code = 60000
            prefix = "P"
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ ประเภทต้องเป็น 'รายวัน' หรือ 'รายเดือน' เท่านั้น")
            )
            return

        existing = worksheet.get_all_values()
        last_row = existing[-1] if len(existing) > 1 else []
        # ดึงเลขจากรหัสล่าสุด (เช่น 'P60001' → 60001)
        if len(last_row) >= 3:
            raw_code = last_row[2]
            number_part = int(re.sub(r'\D', '', raw_code)) if raw_code else default_code
        else:
            number_part = default_code

        new_code = number_part + 1
        emp_code = prefix+str(new_code)

        tz = pytz.timezone('Asia/Bangkok')
        now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")

        worksheet.append_row(["", branch, emp_code, name, nickname, postion, start,"", emp_type, user_id, now])
        import requests


# เรียก Webhook Apps Script
        webhook_url = os.getenv("APPS_SCRIPT_WEBHOOK")
        if webhook_url:
            try:
                sheet_name = worksheet.title  # เช่น "DailyEmployee"
                r = requests.post(webhook_url, json={"sheet": sheet_name})
                
                print("Webhook Response:", r.text)
            except Exception as e:
                print("Webhook Error:", e)

        confirmation_text = (
            f"✅ ลงทะเบียนสำเร็จ\n"
            f"รหัสพนักงาน: {emp_code}\n"
            f"ชื่อ: {name}\n"
            f"ตำแหน่งงาน: {postion}\n"
            f"สาขา: {branch}\n"
            f"วันเริ่มงาน: {start}\n"
            f"📌 โปรดแจ้งหัวหน้างาน/พนักงาน ล่วงหน้าก่อนเริ่มงาน"
        )
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=confirmation_text)
        )

    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"เกิดข้อผิดพลาด: {str(e)}")
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
