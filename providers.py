from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

import config
from net import Net

TRUTHY = {"1", "true", "yes", "y", "on", "live", "active", "alive", "supported", "secure", "enabled"}
FALSY = {"0", "false", "no", "n", "off", "dead", "inactive", "died", "unsupported", "disabled"}


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    return None


def _scan(obj: dict, needles: tuple[str, ...]) -> bool | None:
    for key, value in obj.items():
        lowered = key.lower().replace("_", "").replace("-", "")
        if any(n in lowered for n in needles):
            verdict = _to_bool(value)
            if verdict is not None:
                return verdict
    return None


def _three_ds_from(obj: dict) -> bool | None:
    return _scan(obj, ("3ds", "threeds", "threed", "securecode"))


def _live_from(obj: dict) -> bool | None:
    for key, value in obj.items():
        if key.lower() in {"live", "status", "checked", "state", "active", "is_live"}:
            text = str(value).strip().upper()
            if text in {"LIVE", "ACTIVE", "ALIVE"}:
                return True
            if text in {"DEAD", "INACTIVE", "DIED"}:
                return False
            verdict = _to_bool(value)
            if verdict is not None:
                return verdict
    return None


def parse_binlist(j: dict) -> Optional[dict]:
    if not j.get("scheme"):
        return None
    country = j.get("country") or {}
    bank = j.get("bank") or {}
    return {
        "brand": (j.get("scheme") or "").upper() or None,
        "type": (j.get("type") or "").upper() or None,
        "level": (j.get("brand") or "").upper() or None,
        "prepaid": j.get("prepaid"),
        "country": country.get("name"),
        "country_code": country.get("alpha2"),
        "currency": country.get("currency"),
        "bank": bank.get("name"),
        "bank_url": bank.get("url"),
        "bank_phone": bank.get("phone"),
        "source": "binlist.net",
    }


def parse_handy(j: dict) -> Optional[dict]:
    if str(j.get("Status", "")).upper() != "SUCCESS":
        return None
    bank = j.get("Bank") or {}
    country = j.get("Country") or {}
    return {
        "brand": (j.get("Scheme") or "").upper() or None,
        "type": (j.get("Type") or "").upper() or None,
        "level": (j.get("CardTier") or "").upper() or None,
        "country": country.get("Name"),
        "country_code": country.get("A2") or country.get("Alpha2"),
        "currency": country.get("Currency"),
        "bank": bank.get("Name"),
        "bank_url": bank.get("URL"),
        "bank_phone": bank.get("Phone"),
        "source": "handyapi",
    }


def parse_antipublic(j: dict) -> Optional[dict]:
    if not j.get("brand") and not j.get("bank"):
        return None
    return {
        "brand": (j.get("brand") or "").upper() or None,
        "type": (j.get("type") or "").upper() or None,
        "level": (j.get("level") or "").upper() or None,
        "country": j.get("country"),
        "country_code": j.get("country_code"),
        "currency": j.get("currency"),
        "bank": j.get("bank"),
        "bank_phone": j.get("phone"),
        "three_ds": _three_ds_from(j),
        "live": _live_from(j),
        "source": "antipublic",
    }


def parse_generic(j: Any) -> Optional[dict]:
    if not isinstance(j, dict):
        return None
    for key in ("result", "data", "bin", "info"):
        if isinstance(j.get(key), dict):
            j = j[key]
            break
    out: dict[str, Any] = {"source": "custom"}
    mapping = {
        "brand": ("brand", "scheme", "card_brand", "network"),
        "type": ("type", "card_type", "funding"),
        "level": ("level", "tier", "card_level", "category"),
        "bank": ("bank", "bank_name", "issuer"),
        "country": ("country", "country_name"),
        "country_code": ("country_code", "alpha2", "a2", "iso"),
        "currency": ("currency", "currency_code"),
    }
    flat = {k.lower(): v for k, v in j.items()}
    for target, keys in mapping.items():
        for key in keys:
            if key in flat and flat[key]:
                value = flat[key]
                out[target] = value if isinstance(value, str) else str(value)
                break
    out["three_ds"] = _three_ds_from(j)
    out["live"] = _live_from(j)
    return out if out.get("brand") or out.get("bank") else None


class Provider:
    def __init__(self, name: str, url: str, parser: Callable[[Any], Optional[dict]],
                 headers: dict | None = None) -> None:
        self.name = name
        self.url = url
        self.parser = parser
        self.headers = headers

    async def fetch(self, net: Net, bin_value: str, proxy: str | None) -> Optional[dict]:
        data = await net.get_json(self.url.format(bin=bin_value), proxy=proxy, headers=self.headers)
        if not data:
            return None
        try:
            return self.parser(data)
        except Exception:
            return None


def build_providers() -> list[Provider]:
    providers = [
        Provider("handyapi", "https://data.handyapi.com/bin/{bin}", parse_handy),
        Provider("binlist", "https://lookup.binlist.net/{bin}", parse_binlist),
        Provider("antipublic", "https://bins.antipublic.cc/bins/{bin}", parse_antipublic),
    ]
    if config.CUSTOM_BIN_URL:
        headers = {}
        for pair in config.CUSTOM_BIN_HEADERS.split(";"):
            if ":" in pair:
                k, v = pair.split(":", 1)
                headers[k.strip()] = v.strip()
        providers.append(Provider("custom", config.CUSTOM_BIN_URL, parse_generic, headers or None))
    return providers


def _sample_card(bin_value: str) -> str:
    from utils import luhn_card
    length = 15 if bin_value.startswith(("34", "37")) else 16
    return luhn_card(bin_value, length)


class BinLookup:
    """Races every provider in parallel, merges the richest payload, caches it."""

    def __init__(self, net: Net, db) -> None:
        self.net = net
        self.db = db
        self.providers = build_providers()

    async def check(self, bin_value: str, proxy: str | None = None):
        from checker import BinResult

        cached = await self.db.cache_get(bin_value)
        if cached:
            return BinResult(bin_value, cached["data"], cached["live"], cached["three_ds"], cached=True)

        results = await asyncio.gather(
            *[p.fetch(self.net, bin_value, proxy) for p in self.providers],
            return_exceptions=True,
        )

        merged: dict[str, Any] = {}
        live: bool | None = None
        three_ds: bool | None = None

        for item in results:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if key in ("live", "three_ds"):
                    continue
                if value not in (None, "") and not merged.get(key):
                    merged[key] = value
            if live is None and item.get("live") is not None:
                live = item["live"]
            if three_ds is None and item.get("three_ds") is not None:
                three_ds = item["three_ds"]

        if not merged:
            return BinResult(bin_value, {}, None, None, error="no data")

        merged["bin"] = bin_value
        merged["card"] = merged.get("card") or _sample_card(bin_value)

        await self.db.cache_set(bin_value, merged, live, three_ds, merged.get("source", "merged"))
        return BinResult(bin_value, merged, live, three_ds)