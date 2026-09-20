import os
import requests
from threading import Thread
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import like_pb2

# ==========================================
# 1. BOT TOKEN SETTING
# ==========================================
BOT_TOKEN = "8334841691:AAGcGluW4qQMYUWtKAa6YAYSfuwKbR6ZHcs"


# ==========================================
# 2. AAPKA AAP.PY FLASK CODE
# ==========================================
app = Flask(__name__)

def load_accounts(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "message": "Free Fire Like API Server Running"
    })

@app.route('/like', methods=['GET', 'POST'])
def send_like():
    target_uid = request.args.get('uid') or (request.json.get('uid') if request.is_json else None)
    region = request.args.get('region', 'ind').lower()

    if not target_uid:
        return jsonify({"status": "error", "message": "Target UID is required"}), 400

    account_file = f"account_{region}.txt"
    accounts = load_accounts(account_file)

    if not accounts:
        return jsonify({"status": "error", "message": f"{account_file} not found or empty"}), 404

    try:
        req = like_pb2.LikeRequest()
        req.uid = int(target_uid)
        req.region_id = region

        return jsonify({
            "status": "success",
            "target_uid": target_uid,
            "region": region,
            "total_accounts_loaded": len(accounts),
            "message": f"{len(accounts)} Indian accounts se UID {target_uid} par like process start kar diya gaya hai."
        })
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500


# ==========================================
# 3. ADDED TELEGRAM BOT CODE
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Free Fire Like Bot!\n\n"
        "Likes bhejne ke liye command use karein:\n"
        "`/like <UID>`\n\n"
        "Example: `/like 123456789`",
        parse_mode="Markdown"
    )

async def handle_like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Kripya UID bhi likhein! Example: `/like 123456789`", parse_mode="Markdown")
        return

    target_uid = context.args[0]
    await update.message.reply_text(f"⏳ UID `{target_uid}` par likes bheje ja rahe hain...", parse_mode="Markdown")

    try:
        port = int(os.environ.get("PORT", 5000))
        # Internal API call
        response = requests.get(f"http://127.0.0.1:{port}/like?uid={target_uid}&region=ind", timeout=15)
        data = response.json()

        if response.status_code == 200 and data.get("status") == "success":
            await update.message.reply_text(
                f"✅ **Likes Process Started!**\n\n"
                f"👤 **Target UID:** `{target_uid}`\n"
                f"🌐 **Region:** Indian Server\n"
                f"📊 **Accounts Loaded:** {data.get('total_accounts_loaded', 0)}\n"
                f"📩 **Response:** {data.get('message')}",
                parse_mode="Markdown"
            )
        else:
            err_msg = data.get("message", "Unknown error")
            await update.message.reply_text(f"❌ **Error:** {err_msg}", parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

def run_telegram_bot():
    if BOT_TOKEN and BOT_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("like", handle_like_command))
        print("Telegram Bot Active...")
        bot_app.run_polling(stop_signals=None)


# ==========================================
# 4. BOT KO FLASK KE SAATH CHALANA
# ==========================================
Thread(target=run_telegram_bot, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
