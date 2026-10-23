from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from functools import wraps

from telegram import BotCommand, InputFile, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config
import keyboards as kb
import utils
import views
from checker import BulkRunner, BinResult, ProxyPool
from db import DB
from net import Net
from providers import BinLookup

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("binbot")

RESULT_STORE: dict[str, dict] = {}
RESULT_STORE_ORDER: list[str] = []
MAX_STORED_JOBS = 40


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def safe_edit(message, text: str, markup=None) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML,
                                reply_markup=markup, disable_web_page_preview=True)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.debug("edit failed: %s", exc)
    except RetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass


async def ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db: DB = context.bot_data["db"]
    row = await db.get_user(tg_user.id)
    if row is None:
        row = await db.upsert_user(tg_user.id, tg_user.username, tg_user.first_name)
        if not config.FREE_MODE and config.NOTIFY_ADMIN_ON_JOIN and not is_admin(tg_user.id):
            await notify_admins_new_user(context, row)

    if row["status"] == "banned":
        await update.effective_message.reply_text(
            "⛔️ <b>Access denied.</b> You are banned from this bot.\n"
            f"Contact {config.SUPPORT_HANDLE or 'an admin'} if this is a mistake.",
            parse_mode=ParseMode.HTML)
        return None

    if row["status"] == "pending" and not config.FREE_MODE and not is_admin(tg_user.id):
        await update.effective_message.reply_text(
            "🕐 <b>Access pending</b>\n"
            "──────────────────────────\n"
            f"Your ID: <code>{tg_user.id}</code>\n"
            "An admin must approve you before you can use the bot.",
            parse_mode=ParseMode.HTML)
        return None
    return row


async def notify_admins_new_user(context: ContextTypes.DEFAULT_TYPE, row) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    text = (
        "🆕 <b>New access request</b>\n"
        f"Name: {utils.esc(row['first_name'])}\n"
        f"User: @{utils.esc(row['username'] or '—')}\n"
        f"ID: <code>{row['user_id']}</code>"
    )
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"ap:{row['user_id']}"),
        InlineKeyboardButton("⛔️ Ban", callback_data=f"bn:{row['user_id']}"),
    ]])
    for admin in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass


def guard(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        row = await ensure_user(update, context)
        if row is None:
            return
        context.user_data["row"] = dict(row)
        try:
            return await func(update, context)
        except Exception:
            log.exception("handler %s failed", func.__name__)
            target = update.effective_message
            if target:
                await target.reply_text("⚠️ Internal error. Try again.")
    return wrapper


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.effective_message.reply_text("👑 Admin only.")
            return
        return await func(update, context)
    return wrapper


@guard
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        views.welcome(user.first_name, is_admin(user.id)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.main_menu(is_admin(user.id)),
        disable_web_page_preview=True,
    )


@guard
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        views.help_text(is_admin(update.effective_user.id)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.back(),
    )


@guard
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: <code>/check 457173</code>", parse_mode=ParseMode.HTML)
        return
    bin_value = utils.clean_bin(context.args[0])
    if not bin_value:
        await update.message.reply_text("⚠️ Invalid BIN. Expected 6–8 digits.")
        return

    msg = await update.message.reply_text(f"⏳ <b>Resolving {bin_value}…</b>", parse_mode=ParseMode.HTML)
    lookup: BinLookup = context.bot_data["lookup"]
    proxies = await context.bot_data["db"].alive_proxies(update.effective_user.id)
    pool = ProxyPool(proxies)
    result = await lookup.check(bin_value, proxy=pool.next())
    await safe_edit(msg, views.bin_card(result))
    await context.bot_data["db"].bump_user_stats(update.effective_user.id, 1)


@guard
async def cmd_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>◈ BULK MODE ◈</b>\n"
        "──────────────────────────\n"
        "Send me a <b>.txt</b> file (or paste raw text) containing one BIN or card "
        "number per line.\n\n"
        f"• Up to <b>{utils.human(config.MAX_BINS_PER_JOB)}</b> BINs\n"
        "• Duplicates removed automatically\n"
        "• Only <b>LIVE</b> bins are exported, with 3DS status\n"
        "• Live ETA + progress while running\n\n"
        "<i>Ready when you are.</i>",
        parse_mode=ParseMode.HTML,
    )


@guard
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"🆔 <code>{user.id}</code>\n👤 @{utils.esc(user.username or '—')}",
        parse_mode=ParseMode.HTML)


@guard
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = context.user_data["row"]
    glob = await context.bot_data["db"].global_stats()
    await update.message.reply_text(
        "<b>◈ YOUR STATS ◈</b>\n"
        "──────────────────────────\n"
        f"<code>"
        f"{'Checks':<12}{utils.human(row['checks'])}\n"
        f"{'BINs':<12}{utils.human(row['bins'])}\n"
        f"{'Status':<12}{row['status']}"
        f"</code>\n"
        "──────────────────────────\n"
        f"<b>◈ SYSTEM ◈</b>\n"
        f"<code>"
        f"{'Jobs':<12}{utils.human(glob['jobs'])}\n"
        f"{'BINs':<12}{utils.human(glob['bins'])}\n"
        f"{'Live hits':<12}{utils.human(glob['live'])}"
        f"</code>",
        parse_mode=ParseMode.HTML,
    )


@guard
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if doc.file_size and doc.file_size > 5_000_000:
        await update.message.reply_text("⚠️ File too large (max 5 MB).")
        return
    if not (doc.file_name or "").lower().endswith((".txt", ".csv", ".lst")):
        await update.message.reply_text("⚠️ Send a .txt or .csv file.")
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    tg_file = await doc.get_file()
    raw = bytes(await tg_file.download_as_bytearray()).decode("utf-8", errors="ignore")
    await prepare_job(update, context, raw, source=doc.file_name)


@guard
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    bins = utils.parse_bin_list(text, config.MAX_BINS_PER_JOB + 1)
    if not bins:
        return
    await prepare_job(update, context, text, source="pasted text")


async def prepare_job(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      raw: str, source: str) -> None:
    if context.user_data.get("running"):
        await update.message.reply_text("⚠️ You already have a job running. Use /cancel first.")
        return

    bins = utils.parse_bin_list(raw, config.MAX_BINS_PER_JOB + 1)
    truncated = len(bins) > config.MAX_BINS_PER_JOB
    bins = bins[: config.MAX_BINS_PER_JOB]

    if len(bins) < 1:
        await update.message.reply_text("⚠️ No valid BINs found.")
        return

    context.user_data["pending"] = bins
    context.user_data["pending_source"] = source

    note = ""
    if truncated:
        note = f"\n⚠️ Capped at {utils.human(config.MAX_BINS_PER_JOB)} (extra ignored)."

    await update.message.reply_text(
        "<b>◈ JOB READY ◈</b>\n"
        "──────────────────────────\n"
        f"Source      <code>{utils.esc(source)}</code>\n"
        f"Valid BINs  <b>{utils.human(len(bins))}</b>\n"
        f"Concurrency <b>{config.DEFAULT_CONCURRENCY}</b>{note}\n"
        "──────────────────────────\n"
        "<i>Only LIVE bins with 3DS status will be exported.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.bulk_confirm(len(bins)),
    )


async def run_job(chat_id: int, context: ContextTypes.DEFAULT_TYPE,
                  bins: list[str], user_id: int) -> None:
    db: DB = context.bot_data["db"]
    lookup: BinLookup = context.bot_data["lookup"]

    proxies = await db.alive_proxies(user_id)
    runner = BulkRunner(lookup, bins, config.DEFAULT_CONCURRENCY, proxies)
    job_id = await db.create_job(user_id, len(bins))
    job_key = f"{user_id}:{job_id}"

    context.user_data["running"] = job_key
    context.bot_data["runners"][job_key] = runner

    progress_msg = await context.bot.send_message(
        chat_id,
        views.progress_card(runner.stats, "STARTING…"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.running(job_key),
    )

    last_edit = 0.0

    async def on_progress(stats):
        nonlocal last_edit
        now = time.time()
        if now - last_edit < config.PROGRESS_INTERVAL - 0.3 and stats.done < stats.total:
            return
        last_edit = now
        await safe_edit(progress_msg, views.progress_card(stats), kb.running(job_key))

    stats = await runner.run(on_progress)

    await db.update_job(job_id, done=stats.done, live=stats.live, dead=stats.dead, status="done")
    await db.bump_user_stats(user_id, stats.done)

    results = runner.sorted_results()
    RESULT_STORE[job_key] = {"results": results, "created": time.time()}
    RESULT_STORE_ORDER.append(job_key)
    while len(RESULT_STORE_ORDER) > MAX_STORED_JOBS:
        RESULT_STORE.pop(RESULT_STORE_ORDER.pop(0), None)

    context.user_data.pop("running", None)
    context.bot_data["runners"].pop(job_key, None)

    live_3ds = sum(1 for r in results if r.live and r.three_ds)
    live_all = sum(1 for r in results if r.live)
    dead = sum(1 for r in results if r.live is False)

    await safe_edit(progress_msg, views.summary_card(stats),
                    kb.result_menu(job_key, live_all, live_3ds, dead))


def _row(result: BinResult) -> str:
    d = result.data or {}
    tds = "3DS" if result.three_ds else ("NO3DS" if result.three_ds is False else "UNKNOWN")
    return "|".join([
        result.bin,
        d.get("brand") or "",
        d.get("type") or "",
        d.get("level") or "",
        d.get("bank") or "",
        d.get("country_code") or "",
        tds,
        d.get("currency") or "",
    ])


HEADER = "BIN|BRAND|TYPE|LEVEL|BANK|COUNTRY|3DS|CURRENCY"


def build_export(results: list[BinResult], kind: str) -> tuple[bytes, str]:
    if kind == "l3":
        rows = [r for r in results if r.live and r.three_ds]
        return ("\n".join([HEADER] + [_row(r) for r in rows])).encode(), "live_3ds.txt"
    if kind == "lv":
        rows = [r for r in results if r.live]
        return ("\n".join([HEADER] + [_row(r) for r in rows])).encode(), "live_all.txt"
    if kind == "dd":
        rows = [r for r in results if r.live is False]
        return ("\n".join([HEADER] + [_row(r) for r in rows])).encode(), "dead.txt"
    if kind == "csv":
        rows = [r for r in results if r.live]
        return ("\n".join([HEADER.replace("|", ",")] +
                          [_row(r).replace("|", ",") for r in rows])).encode(), "live_report.csv"
    payload = [
        {"bin": r.bin, "status": r.status, "three_ds": r.three_ds, **(r.data or {})}
        for r in results
    ]
    return json.dumps(payload, indent=2).encode(), "full_report.json"


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    user_id = query.from_user.id
    await query.answer()

    if data.startswith(("ap:", "bn:")):
        action, target = data.split(":")
        await context.bot_data["db"].set_status(int(target), "approved" if action == "ap" else "banned")
        await safe_edit(query.message,
                        f"{'✅ Approved' if action == 'ap' else '⛔️ Banned'} <code>{target}</code>")
        return

    row = await context.bot_data["db"].get_user(user_id)
    if row and row["status"] == "banned":
        return

    if data == "m:home":
        await safe_edit(query.message, views.welcome(query.from_user.first_name, is_admin(user_id)),
                        kb.main_menu(is_admin(user_id)))
    elif data == "m:check":
        await safe_edit(query.message,
                        "Send <code>/check &lt;bin&gt;</code> — e.g. <code>/check 457173</code>",
                        kb.back())
    elif data == "m:bulk":
        await safe_edit(query.message,
                        "<b>◈ BULK MODE ◈</b>\n──────────────────────────\n"
                        "Send a <b>.txt</b> file with one BIN per line.\n"
                        f"Max <b>{utils.human(config.MAX_BINS_PER_JOB)}</b> · duplicates removed.",
                        kb.back())
    elif data == "m:help":
        await safe_edit(query.message, views.help_text(is_admin(user_id)), kb.back())
    elif data == "m:stats":
        glob = await context.bot_data["db"].global_stats()
        await safe_edit(query.message,
                        f"<b>◈ SYSTEM STATS ◈</b>\n──────────────────────────\n"
                        f"<code>Jobs   {utils.human(glob['jobs'])}\n"
                        f"BINs   {utils.human(glob['bins'])}\n"
                        f"Live   {utils.human(glob['live'])}</code>",
                        kb.back())
    elif data == "px:menu":
        rows = await context.bot_data["db"].list_proxies(user_id, False)
        await safe_edit(query.message, views.proxy_card(rows), kb.proxy_menu(is_admin(user_id)))
    elif data == "px:add":
        await safe_edit(query.message,
                        "Add proxies, one per line:\n"
                        "<code>/addproxy 1.2.3.4:8080</code>\n"
                        "<code>/addproxy user:pass@1.2.3.4:8080</code>\n"
                        "<code>/addproxy socks5://user:pass@1.2.3.4:1080</code>",
                        kb.back("px:menu"))
    elif data == "px:test":
        await query.message.reply_text("🧪 Testing proxies…")
        await test_proxies_for(context, user_id, query.message)
    elif data == "px:clear":
        await context.bot_data["db"].clear_proxies(user_id, only_user=True)
        rows = await context.bot_data["db"].list_proxies(user_id, False)
        await safe_edit(query.message, views.proxy_card(rows), kb.proxy_menu(is_admin(user_id)))
    elif data == "px:clearglobal":
        await context.bot_data["db"].clear_proxies(0, only_user=False)
        rows = await context.bot_data["db"].list_proxies(user_id, False)
        await safe_edit(query.message, views.proxy_card(rows), kb.proxy_menu(is_admin(user_id)))
    elif data == "j:start":
        bins = context.user_data.get("pending") or []
        if not bins:
            await safe_edit(query.message, "⚠️ Session expired. Send the file again.", kb.back("m:bulk"))
            return
        context.user_data.pop("pending", None)
        await safe_edit(query.message, "🚀 Job queued…")
        asyncio.create_task(run_job(query.message.chat_id, context, bins, user_id))
    elif data == "j:cancel":
        context.user_data.pop("pending", None)
        await safe_edit(query.message, "✖️ Cancelled.", kb.back("m:home"))
    elif data.startswith("j:stop:"):
        key = data.split(":", 2)[2]
        runner = context.bot_data["runners"].get(key)
        if runner:
            runner.cancelled = True
            await query.answer("Stopping…", show_alert=False)
    elif data.startswith("dl:"):
        _, kind, job_key = data.split(":", 2)
        entry = RESULT_STORE.get(job_key)
        if not entry:
            await query.message.reply_text("⚠️ Result expired.")
            return
        payload, filename = build_export(entry["results"], kind)
        if not payload.strip():
            await query.message.reply_text("📭 No rows matched that export.")
            return
        await query.message.reply_document(
            InputFile(io.BytesIO(payload), filename=filename),
            caption=f"<b>{utils.esc(filename)}</b>\n<code>{len(payload.splitlines()) - 1} entries</code>",
            parse_mode=ParseMode.HTML,
        )
    elif data.startswith("us:"):
        if data == "us:noop":
            return
        await render_users(query, context, int(data.split(":")[1]))
    elif data == "ad:menu":
        await safe_edit(query.message, "<b>👑 ADMIN PANEL</b>", kb.admin_menu())
    elif data == "ad:users":
        await render_users(query, context, 0)
    elif data == "ad:stats":
        counts = await context.bot_data["db"].count_users()
        glob = await context.bot_data["db"].global_stats()
        await safe_edit(query.message,
                        f"<b>👑 SYSTEM OVERVIEW</b>\n──────────────────────────\n"
                        f"<code>Users    {counts['total']}\n"
                        f"Approved {counts['approved']}\n"
                        f"Pending  {counts['pending']}\n"
                        f"Banned   {counts['banned']}\n"
                        f"Jobs     {utils.human(glob['jobs'])}\n"
                        f"BINs     {utils.human(glob['bins'])}</code>",
                        kb.back("ad:menu"))


async def render_users(query, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    per_page = 8
    db: DB = context.bot_data["db"]
    counts = await db.count_users()
    pages = max(1, (counts["total"] + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    rows = await db.list_users(page * per_page, per_page)

    lines = []
    for row in rows:
        icon = {"approved": "🟢", "pending": "🕐", "banned": "⛔️"}.get(row["status"], "⚪️")
        lines.append(f"{icon} <code>{row['user_id']}</code> @{utils.esc(row['username'] or '—')} "
                     f"· {utils.human(row['bins'])} bins")
    await safe_edit(query.message,
                    f"<b>👥 USERS</b>  ({counts['total']} total)\n──────────────────────────\n"
                    + ("\n".join(lines) or "<i>empty</i>"),
                    kb.users_nav(page, pages))


def normalize_proxy(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return None


@guard
async def cmd_addproxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if config.ADMIN_ONLY_PROXIES and not is_admin(update.effective_user.id):
        await update.message.reply_text("👑 Only admins can manage proxies.")
        return
    payload = update.message.text.partition(" ")[2]
    if not payload and update.message.reply_to_message:
        payload = update.message.reply_to_message.text or ""
    if not payload:
        await update.message.reply_text("Usage: <code>/addproxy host:port:user:pass</code>",
                                        parse_mode=ParseMode.HTML)
        return

    tokens = [t for t in payload.replace("\n", ",").replace(" ", ",").split(",") if t.strip()]
    added = dupes = bad = 0
    for token in tokens:
        url = normalize_proxy(token)
        if not url:
            bad += 1
            continue
        ok = await context.bot_data["db"].add_proxy(url, update.effective_user.id, False)
        added += 1 if ok else 0
        dupes += 0 if ok else 1
    await context.bot_data["db"].conn.commit()
    await update.message.reply_text(
        f"✅ Added <b>{added}</b> · duplicates <b>{dupes}</b> · invalid <b>{bad}</b>\n"
        "<i>Run /testproxies to verify health.</i>",
        parse_mode=ParseMode.HTML)


@guard
async def cmd_delproxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: <code>/delproxy &lt;id&gt;</code>")
        return
    try:
        await context.bot_data["db"].delete_proxy(int(context.args[0]))
    except ValueError:
        await update.message.reply_text("⚠️ ID must be a number.")
        return
    await update.message.reply_text("🗑 Deleted.")


@guard
async def cmd_testproxies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🧪 Testing proxies…")
    await test_proxies_for(context, update.effective_user.id, msg)


async def test_proxies_for(context: ContextTypes.DEFAULT_TYPE, user_id: int, msg) -> None:
    db: DB = context.bot_data["db"]
    net: Net = context.bot_data["net"]
    rows = await db.list_proxies(user_id, False)
    if not rows:
        await safe_edit(msg, "📭 No proxies configured.")
        return

    async def probe(row):
        start = time.perf_counter()
        data = await net.get_json(config.PROXY_TEST_URL, proxy=row["url"], retries=0)
        latency = int((time.perf_counter() - start) * 1000)
        alive = data is not None
        await db.update_proxy(row["id"], alive, latency if alive else None)
        return row["url"], alive, latency

    results = await asyncio.gather(*[probe(r) for r in rows])
    alive = sum(1 for _, ok, _ in results if ok)
    lines = [
        f"{'🟢' if ok else '🔴'} <code>{utils.esc(url)}</code> {f'{lat}ms' if ok else ''}"
        for url, ok, lat in results
    ]
    await safe_edit(msg,
                    f"<b>◈ PROXY HEALTH ◈</b>\n──────────────────────────\n"
                    f"Alive <b>{alive}</b> / {len(results)}\n──────────────────────────\n"
                    + "\n".join(lines[:30]))


@guard
async def cmd_clearproxies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    removed = await context.bot_data["db"].clear_proxies(update.effective_user.id, only_user=True)
    await update.message.reply_text(f"🧹 Removed <b>{removed}</b> proxies.", parse_mode=ParseMode.HTML)


@guard
@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    per_page = 8
    db: DB = context.bot_data["db"]
    counts = await db.count_users()
    rows = await db.list_users(0, per_page)
    lines = [
        f"{ {'approved':'🟢','pending':'🕐','banned':'⛔️'}.get(r['status'],'⚪️') } "
        f"<code>{r['user_id']}</code> @{utils.esc(r['username'] or '—')}"
        for r in rows
    ]
    pages = max(1, (counts["total"] + per_page - 1) // per_page)
    await update.message.reply_text(
        f"<b>👥 USERS</b> ({counts['total']})\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=kb.users_nav(0, pages))


async def _set_status(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str) -> None:
    if not context.args:
        await update.message.reply_text("Usage: <code>/approve &lt;id&gt;</code>")
        return
    await context.bot_data["db"].set_status(int(context.args[0]), status)
    await update.message.reply_text(f"✅ <code>{context.args[0]}</code> → <b>{status}</b>",
                                    parse_mode=ParseMode.HTML)


@guard
@admin_only
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_status(update, context, "approved")


@guard
@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_status(update, context, "banned")


@guard
@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_status(update, context, "approved")


@guard
@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.partition(" ")[2]
    if not text:
        await update.message.reply_text("Usage: <code>/broadcast &lt;text&gt;</code>")
        return
    db: DB = context.bot_data["db"]
    sent = failed = 0
    offset = 0
    while True:
        rows = await db.list_users(offset, 50)
        if not rows:
            break
        for row in rows:
            try:
                await context.bot.send_message(row["user_id"], text, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        offset += 50
    await update.message.reply_text(f"📣 Sent <b>{sent}</b> · failed <b>{failed}</b>",
                                    parse_mode=ParseMode.HTML)


@guard
@admin_only
async def cmd_globalstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    counts = await context.bot_data["db"].count_users()
    glob = await context.bot_data["db"].global_stats()
    await update.message.reply_text(
        f"<b>👑 GLOBAL</b>\n<code>Users {counts['total']}\n"
        f"Jobs {glob['jobs']}\nBINs {utils.human(glob['bins'])}</code>",
        parse_mode=ParseMode.HTML)


@guard
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = context.user_data.get("running")
    if not key:
        await update.message.reply_text("No active job.")
        return
    runner = context.bot_data["runners"].get(key)
    if runner:
        runner.cancelled = True
    await update.message.reply_text("⏹ Cancelling…")


@guard
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    start = time.perf_counter()
    msg = await update.message.reply_text("🏓")
    await msg.edit_text(f"🏓 Pong · <code>{int((time.perf_counter() - start) * 1000)}ms</code>",
                        parse_mode=ParseMode.HTML)


@guard
async def on_proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await context.bot_data["db"].list_proxies(update.effective_user.id, False)
    await update.message.reply_text(
        views.proxy_card(rows),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.proxy_menu(is_admin(update.effective_user.id)),
    )


async def post_init(app: Application) -> None:
    await app.bot_data["db"].connect()
    await app.bot.set_my_commands([
        BotCommand("start", "Main menu"),
        BotCommand("check", "Check a single BIN"),
        BotCommand("bulk", "Bulk check via .txt"),
        BotCommand("proxies", "Proxy manager"),
        BotCommand("addproxy", "Add proxies"),
        BotCommand("testproxies", "Test proxy health"),
        BotCommand("stats", "Your usage stats"),
        BotCommand("cancel", "Stop the running job"),
        BotCommand("help", "Command manual"),
    ])
    log.info("%s online. Admins: %s", config.BOT_NAME, config.ADMIN_IDS)


async def post_shutdown(app: Application) -> None:
    await app.bot_data["net"].close()
    await app.bot_data["db"].close()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("unhandled: %s", context.error)


def main() -> None:
    db = DB()
    net = Net(config.REQUEST_TIMEOUT)

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(16)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data["db"] = db
    app.bot_data["net"] = net
    app.bot_data["lookup"] = BinLookup(net, db)
    app.bot_data["runners"] = {}

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("bulk", cmd_bulk))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("proxies", on_proxies_command))
    app.add_handler(CommandHandler("addproxy", cmd_addproxy))
    app.add_handler(CommandHandler("delproxy", cmd_delproxy))
    app.add_handler(CommandHandler("testproxies", cmd_testproxies))
    app.add_handler(CommandHandler("clearproxies", cmd_clearproxies))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("globalstats", cmd_globalstats))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(error_handler)

    log.info("Polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()