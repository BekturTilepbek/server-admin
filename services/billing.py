"""
Сервис мониторинга биллинга DigitalOcean.

Тянет статус аккаунта и баланс по REST API DO (несколько аккаунтов = несколько
персональных токенов). Чтобы не тащить лишних зависимостей (в requirements нет
ни requests, ни aiohttp), используем стандартный urllib в отдельном потоке —
тот же паттерн, что и с синхронным SSH: блокирующий вызов оборачивается в
asyncio.to_thread(...), event loop не блокируется.

Документация: https://docs.digitalocean.com/reference/api/reference/billing/
Лимиты DO: 5000 req/час, 250 req/мин — для нескольких аккаунтов раз в сутки
запас огромный.
"""
import json
import asyncio
import logging
import urllib.request
import urllib.error

from config import DO_ACCOUNTS

logger = logging.getLogger(__name__)

API_BASE = "https://api.digitalocean.com/v2"
HTTP_TIMEOUT = 15  # сек на запрос, чтобы не зависнуть навечно


def _get_json_sync(path: str, token: str) -> dict:
    """Синхронный GET к DO API. Вызывать только через asyncio.to_thread."""
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _get_json(path: str, token: str) -> dict:
    return await asyncio.to_thread(_get_json_sync, path, token)


async def fetch_account(label: str, token: str) -> dict:
    """
    Возвращает сводку по одному аккаунту:
      {
        "label": str,
        "ok": bool,                # удалось ли опросить
        "status": str | None,      # active / warning / locked
        "month_to_date": str | None,
        "account_balance": str | None,
        "error": str | None,
      }
    Сетевые/HTTP ошибки не пробрасываются наружу — пишем в error, чтобы один
    мёртвый токен не ронял весь дайджест.
    """
    result = {
        "label": label,
        "ok": False,
        "status": None,
        "month_to_date": None,
        "account_balance": None,
        "error": None,
    }
    try:
        account = await _get_json("/account", token)
        result["status"] = account.get("account", {}).get("status")

        balance = await _get_json("/customers/my/balance", token)
        result["month_to_date"] = balance.get("month_to_date_balance")
        result["account_balance"] = balance.get("account_balance")

        result["ok"] = True
    except urllib.error.HTTPError as e:
        if e.code == 401:
            result["error"] = "неверный или отозванный токен (401)"
        elif e.code == 429:
            result["error"] = "превышен лимит запросов (429)"
        else:
            result["error"] = f"HTTP {e.code}"
        logger.warning("Биллинг DO: %s — %s", label, result["error"])
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Биллинг DO: %s — ошибка опроса: %s", label, e)
    return result


async def get_all_accounts_billing() -> list[dict]:
    """Параллельно опрашивает все настроенные аккаунты DO."""
    if not DO_ACCOUNTS:
        return []
    tasks = [fetch_account(label, token) for label, token in DO_ACCOUNTS]
    return await asyncio.gather(*tasks, return_exceptions=False)


def _money(value: str | None) -> str:
    """DO отдаёт суммы строками вида '152.34'. Форматируем в $152.34."""
    if value is None:
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _status_icon(status: str | None) -> str:
    return {"active": "🟢", "warning": "🟡", "locked": "🔴"}.get(status, "❓")


def format_digest(accounts: list[dict]) -> str:
    """Человекочитаемая сводка по всем аккаунтам для Telegram (HTML)."""
    if not accounts:
        return "⚠️ Аккаунты DigitalOcean не настроены (DO_ACCOUNTS в .env пуст)."

    lines = ["💳 <b>Биллинг DigitalOcean</b>\n"]
    for acc in accounts:
        label = acc["label"]
        if not acc["ok"]:
            lines.append(f"❓ <b>{label}</b>: ошибка опроса — {acc['error']}")
            continue

        icon = _status_icon(acc["status"])
        status_txt = acc["status"] or "неизвестно"
        lines.append(
            f"{icon} <b>{label}</b> — {status_txt}\n"
            f"   💸 Потрачено за месяц: <b>{_money(acc['month_to_date'])}</b>\n"
            f"   📊 Баланс аккаунта: {_money(acc['account_balance'])}"
        )
    return "\n".join(lines)


def has_problem(accounts: list[dict]) -> bool:
    """True, если хотя бы один аккаунт не в статусе active (или не опросился)."""
    for acc in accounts:
        if not acc["ok"] or acc["status"] != "active":
            return True
    return False