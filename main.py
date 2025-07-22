import os
import sqlite3
import difflib
from cryptography.fernet import Fernet
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

# === 🔐 Configuration ===
TOKEN =  " Replace with your Telegram bot token"
ADMIN_PASSWORD =   "Set your admin login password"
AUTHORIZED_USERS = set()  # Stores authenticated user IDs

# === 🔑 Encryption Setup ===
KEY_FILE = "secret.key"
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "wb") as f:
        f.write(Fernet.generate_key())
with open(KEY_FILE, "rb") as f:
    cipher = Fernet(f.read())

# === 🗃️ Database Setup ===
def initialize_db():
    conn = sqlite3.connect("file_storage.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            content BLOB NOT NULL,
            uploader_id INTEGER,
            uploader_name TEXT
        )
    ''')
    try:
        cursor.execute("ALTER TABLE files ADD COLUMN uploader_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE files ADD COLUMN uploader_name TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

# === 🔐 Access Control ===
async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔐 Usage: /login yourpassword")
        return
    if context.args[0] == ADMIN_PASSWORD:
        user_id = update.message.from_user.id
        AUTHORIZED_USERS.add(user_id)
        await update.message.reply_text("✅ Access granted! You can now upload and delete files.")
    else:
        await update.message.reply_text("❌ Incorrect password.")

# === 💾 Database Helpers ===
def save_file(name, content, user_id, user_name):
    encrypted = cipher.encrypt(bytes(content))
    conn = sqlite3.connect("file_storage.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO files (name, content, uploader_id, uploader_name) VALUES (?, ?, ?, ?)",
                   (name, encrypted, user_id, user_name))
    conn.commit()
    conn.close()

def get_file(name):
    name = name.strip().lower()
    conn = sqlite3.connect("file_storage.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, content FROM files")
    results = cursor.fetchall()
    conn.close()

    for stored_name, content in results:
        if stored_name.strip().lower() == name:
            return cipher.decrypt(content)
    return None

def delete_file(name):
    name = name.strip().lower()
    conn = sqlite3.connect("file_storage.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM files WHERE LOWER(name) = ?", (name,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def list_files():
    conn = sqlite3.connect("file_storage.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM files")
    names = cursor.fetchall()
    conn.close()
    return [n[0] for n in names]

def suggest_filename(name):
    stored_names = list_files()
    return difflib.get_close_matches(name, stored_names, n=1, cutoff=0.5)

# === 🤖 Bot Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Candy Bot!\n"
        "🤖 This bot is owned and operated by Dragon🐉.\n"
        "📤 Send a file to upload *(login required)*\n"
        "📥 /get filename.extension → download\n"
        "📃 /list → view available files\n"
        "🗑️ /delete filename.extension *(login required)*\n"
        "🔐 /login yourpassword → gain access\n"
        "🔒 /logout → to logout",
        parse_mode="Markdown"
    )

async def store_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in AUTHORIZED_USERS:
        await update.message.reply_text("🔐 You must /login before uploading files.")
        return

    file = update.message.document
    if file.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("⚠️ File too large (limit is 5MB).")
        return
    if not (file.mime_type.startswith("application") or file.mime_type.startswith("image")):
        await update.message.reply_text("⚠️ Only documents and images allowed.")
        return

    telegram_file = await file.get_file()
    content = await telegram_file.download_as_bytearray()

    save_file(file.file_name, content, user.id, user.username or "anonymous")
    await update.message.reply_text(
        f"✅ File *'{file.file_name}'* uploaded and encrypted.\n"
        f"👤 Uploader: @{user.username or 'anonymous'}",
        parse_mode="Markdown"
    )

async def get_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📥 Usage: /get filename.ext")
        return
    filename = " ".join(context.args).strip()
    try:
        content = get_file(filename)
        if content:
            await update.message.reply_document(document=content, filename=filename)
        else:
            suggestion = suggest_filename(filename)
            if suggestion:
                await update.message.reply_text(f"❌ File not found. Did you mean *{suggestion[0]}*?", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ File not found. Use /list to view stored files.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error retrieving file: {e}")

async def list_documents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = list_files()
    if files:
        listing = "\n".join(f"• `{name}`" for name in files)
        await update.message.reply_text(f"📁 *Available Files:*\n{listing}", parse_mode="Markdown")
    else:
        await update.message.reply_text("📂 No files stored yet.")

async def list_documents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = list_files()
    if not files:
        await update.message.reply_text("📂 No files stored.")
        return

    keyboard = [
        [InlineKeyboardButton(f"📄 {name}", callback_data=f"GETFILE:{name}")]
        for name in files
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📃 *Tap a file to download:*", reply_markup=reply_markup, parse_mode="Markdown")

async def delete_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in AUTHORIZED_USERS:
        await update.message.reply_text("🔐 You must /login before deleting files.")
        return
    if not context.args:
        await update.message.reply_text("🗑️ Usage: /delete filename.ext")
        return

    filename = " ".join(context.args).strip()
    if delete_file(filename):
        await update.message.reply_text(f"🗑️ File *'{filename}'* deleted successfully.", parse_mode="Markdown")
    else:
        suggestion = suggest_filename(filename)
        if suggestion:
            await update.message.reply_text(f"❌ File not found. Did you mean *{suggestion[0]}*?", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ File not found. Use /list to see stored names.")

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(user_id)
        await update.message.reply_text("🚪 You have been logged out. Upload and delete access revoked.")
    else:
        await update.message.reply_text("ℹ️ You were not logged in.")

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("GETFILE:"):
        filename = query.data.split(":", 1)[1]
        content = get_file(filename)
        if content:
            await query.message.reply_document(document=content, filename=filename)
        else:
            await query.message.reply_text("❌ File not found.")


# === 🚀 Entry Point ===
def main():
    initialize_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("list", list_documents))
    app.add_handler(CommandHandler("get", get_document))
    app.add_handler(CommandHandler("delete", delete_document))
    app.add_handler(MessageHandler(filters.Document.ALL, store_document))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(CommandHandler("logout", logout))

    print("🤖 Bot is running... Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
