from __future__ import annotations

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Markup


def main_menu(is_admin: bool = False) -> Markup:
    rows = [
        [Btn("🔍 Check BIN", callback_data="m:check"), Btn("📁 Bulk Check", callback_data="m:bulk")],
        [Btn("🔌 Proxies", callback_data="px:menu"), Btn("📊 My Stats", callback_data="m:stats")],
        [Btn("ℹ️ Help", callback_data="m:help")],
    ]
    if is_admin:
        rows.append([Btn("👑 Admin Panel", callback_data="ad:menu")])
    return Markup(rows)


def back(target: str = "m:home") -> Markup:
    return Markup([[Btn("◀️ Back", callback_data=target)]])


def bulk_confirm(count: int) -> Markup:
    return Markup([
        [Btn(f"▶️ Start ({count:,} BINs)", callback_data="j:start"),
         Btn("✖️ Cancel", callback_data="j:cancel")],
    ])


def running(job_key: str) -> Markup:
    return Markup([[Btn("⏹ Stop", callback_data=f"j:stop:{job_key}")]])


def result_menu(job_key: str, live: int, live_3ds: int, dead: int) -> Markup:
    return Markup([
        [Btn(f"📄 Live + 3DS ({live_3ds})", callback_data=f"dl:l3:{job_key}"),
         Btn(f"📄 All Live ({live})", callback_data=f"dl:lv:{job_key}")],
        [Btn(f"📄 Dead ({dead})", callback_data=f"dl:dd:{job_key}"),
         Btn("📊 CSV", callback_data=f"dl:csv:{job_key}")],
        [Btn("📦 Full JSON", callback_data=f"dl:js:{job_key}"),
         Btn("🔄 New Job", callback_data="m:bulk")],
    ])


def proxy_menu(is_admin: bool) -> Markup:
    rows = [
        [Btn("➕ Add", callback_data="px:add"), Btn("🧪 Test All", callback_data="px:test")],
        [Btn("🔄 Refresh", callback_data="px:menu"), Btn("🧹 Clear Mine", callback_data="px:clear")],
    ]
    if is_admin:
        rows.append([Btn("🌐 Clear Global", callback_data="px:clearglobal")])
    rows.append([Btn("◀️ Back", callback_data="m:home")])
    return Markup(rows)


def users_nav(page: int, pages: int) -> Markup:
    nav = []
    if page > 0:
        nav.append(Btn("◀️", callback_data=f"us:{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", callback_data="us:noop"))
    if page < pages - 1:
        nav.append(Btn("▶️", callback_data=f"us:{page + 1}"))
    return Markup([nav, [Btn("🔄 Refresh", callback_data=f"us:{page}")]])


def admin_menu() -> Markup:
    return Markup([
        [Btn("👥 Users", callback_data="ad:users"), Btn("📈 Stats", callback_data="ad:stats")],
        [Btn("🌐 Global Proxies", callback_data="px:menu")],
        [Btn("◀️ Back", callback_data="m:home")],
    ])