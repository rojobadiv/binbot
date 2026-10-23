from __future__ import annotations

import config
import utils
from checker import BinResult, Stats

THIN = "─" * 26


def _badge_live(live: bool | None) -> str:
    if live is True:
        return "🟢 <b>LIVE</b>"
    if live is False:
        return "🔴 <b>DEAD</b>"
    return "⚪️ <b>UNKNOWN</b>"


def _badge_3ds(tds: bool | None) -> str:
    if tds is True:
        return "🔒 <b>3DS ENABLED</b>"
    if tds is False:
        return "🔓 <b>NO 3DS</b>"
    return "❔ <b>3DS UNKNOWN</b>"


def bin_card(result: BinResult) -> str:
    d = result.data or {}
    brand = d.get("brand")
    cc = d.get("country_code")
    rows = [
        ("BIN", result.bin),
        ("BRAND", f"{utils.brand_emoji(brand)} {brand or '—'}"),
        ("TYPE", d.get("type") or "—"),
        ("LEVEL", d.get("level") or "—"),
        ("BANK", d.get("bank") or "—"),
        ("COUNTRY", f"{utils.flag(cc)} {d.get('country') or '—'}"),
        ("CURRENCY", d.get("currency") or "—"),
        ("SAMPLE", d.get("card") or "—"),
    ]
    grid = "\n".join(f"{k:<9}{utils.esc(v)}" for k, v in rows)

    status = f"{_badge_live(result.live)}\n{_badge_3ds(result.three_ds)}"
    source = f"<i>source: {utils.esc(d.get('source', 'n/a'))}"
    if result.cached:
        source += " · cached"
    source += "</i>"

    return f"<b>┌─ BIN DETAILS</b>\n<pre>{grid}</pre>\n{status}\n{THIN}\n{source}"


def progress_card(stats: Stats, label: str = "CHECKING BINS") -> str:
    pct = stats.percent
    return (
        f"<b>⏳ {label}</b>\n"
        f"<code>{utils.bar(pct)}  {pct:5.1f}%</code>\n"
        f"<code>"
        f"{'Checked':<11}{utils.human(stats.done)} / {utils.human(stats.total)}\n"
        f"{'Remaining':<11}{utils.human(stats.remaining)}\n"
        f"{'ETA':<11}{utils.fmt_secs(stats.eta)}\n"
        f"{'Rate':<11}{stats.rate:0.1f} bins/s\n"
        f"{'Elapsed':<11}{utils.fmt_secs(stats.elapsed)}"
        f"</code>\n"
        f"{THIN}\n"
        f"🟢 Live <b>{utils.human(stats.live)}</b>   "
        f"🔴 Dead <b>{utils.human(stats.dead)}</b>   "
        f"⚪️ <b>{utils.human(stats.unknown)}</b>\n"
        f"🔒 3DS <b>{utils.human(stats.tds_yes)}</b>   "
        f"🔓 Non-3DS <b>{utils.human(stats.tds_no)}</b>"
    )


def summary_card(stats: Stats) -> str:
    hit_rate = (stats.live / stats.done * 100) if stats.done else 0
    return (
        f"<b>✅ JOB COMPLETE</b>\n"
        f"{THIN}\n"
        f"<code>"
        f"{'Processed':<13}{utils.human(stats.done)}\n"
        f"{'Live':<13}{utils.human(stats.live)}\n"
        f"{'Dead':<13}{utils.human(stats.dead)}\n"
        f"{'Unknown':<13}{utils.human(stats.unknown)}\n"
        f"{'Errors':<13}{utils.human(stats.errors)}\n"
        f"{'3DS':<13}{utils.human(stats.tds_yes)}\n"
        f"{'Hit rate':<13}{hit_rate:0.1f}%\n"
        f"{'Duration':<13}{utils.fmt_secs(stats.elapsed)}\n"
        f"{'Avg rate':<13}{stats.rate:0.1f} bins/s"
        f"</code>\n"
        f"{THIN}\n"
        f"<i>Pick an export below. Default file contains only LIVE bins with 3DS status.</i>"
    )


def welcome(name: str, is_admin: bool) -> str:
    role = " 👑" if is_admin else ""
    return (
        f"<b>◈ {config.BOT_NAME} ◈</b>\n"
        f"{THIN}\n"
        f"Welcome, <b>{utils.esc(name)}</b>{role}\n"
        f"High-speed BIN intelligence: issuer data, liveness and 3DS "
        f"detection, with bulk processing up to "
        f"<b>{utils.human(config.MAX_BINS_PER_JOB)}</b> BINs per file.\n"
        f"{THIN}\n"
        f"<code>/check 457173</code> — single BIN\n"
        f"<code>/bulk</code> — send a .txt file\n"
        f"<code>/proxies</code> — proxy manager\n"
        f"<code>/stats</code> — your usage\n"
        f"<code>/help</code> — full manual"
    )


def help_text(is_admin: bool) -> str:
    base = (
        f"<b>◈ COMMAND MANUAL ◈</b>\n"
        f"{THIN}\n"
        f"<b>Checking</b>\n"
        f"<code>/check &lt;bin&gt;</code> — lookup a single BIN\n"
        f"<code>/bulk</code> — instructions for bulk mode\n"
        f"Send a <b>.txt</b> file (one BIN or card per line) to run bulk.\n"
        f"Limit: <b>{utils.human(config.MAX_BINS_PER_JOB)}</b> BINs, "
        f"duplicates auto-removed.\n\n"
        f"<b>Proxies</b>\n"
        f"<code>/proxies</code> — open the manager\n"
        f"<code>/addproxy host:port:user:pass</code>\n"
        f"<code>/addproxy socks5://user:pass@host:port</code>\n"
        f"<code>/testproxies</code> — latency + health check\n"
        f"<code>/clearproxies</code> — wipe your pool\n\n"
        f"<b>Misc</b>\n"
        f"<code>/stats</code> — usage counters\n"
        f"<code>/id</code> — your Telegram ID"
    )
    if is_admin:
        base += (
            f"\n\n<b>👑 Admin</b>\n"
            f"<code>/users</code> — user browser\n"
            f"<code>/approve &lt;id&gt;</code> / <code>/ban &lt;id&gt;</code> / <code>/unban &lt;id&gt;</code>\n"
            f"<code>/globalstats</code> — system totals\n"
            f"<code>/broadcast &lt;text&gt;</code>"
        )
    return base


def proxy_card(rows: list) -> str:
    if not rows:
        return (
            "<b>◈ PROXY MANAGER ◈</b>\n" + THIN + "\n"
            "<i>No proxies configured. Traffic goes direct.</i>\n\n"
            "Add with <code>/addproxy host:port:user:pass</code>"
        )
    lines = []
    for row in rows[:30]:
        scope = "🌐" if row["is_global"] else "👤"
        state = "🟢" if row["alive"] else "🔴"
        latency = f"{row['latency_ms']}ms" if row["latency_ms"] else "—"
        lines.append(f"{state} {scope} <code>{utils.esc(row['url'])}</code> · {latency}")
    extra = "" if len(rows) <= 30 else f"\n<i>…and {len(rows) - 30} more</i>"
    return (
        f"<b>◈ PROXY MANAGER ◈</b>\n{THIN}\n"
        f"Total: <b>{len(rows)}</b> · Alive: <b>{sum(1 for r in rows if r['alive'])}</b>\n"
        f"{THIN}\n" + "\n".join(lines) + extra
    )