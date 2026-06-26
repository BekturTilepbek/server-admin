"""
Сервис мониторинга биллинга DigitalOcean.

Использует только /v2/customers/my/balance — единственный эндпоинт, который
гарантированно доступен при создании токена через Organization/API.
Эндпоинт /v2/account требует отдельного scope account:read, который при
Organization-токенах часто недоступен (403), поэтому мы его не используем.

Статус аккаунта определяем косвенно по полю account_balance:
  - отрицательное значение = есть задолженность = риск suspend.

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


def _balance_status(account_balance: str | None) -> str:
    """
    Определяет статус по балансу аккаунта (без /account endpoint).

    DO хранит баланс как строку:
      "0.00"   — чисто, задолженности нет
      "-45.20" — есть долг, списание не прошло → риск suspend
      "10.00"  — положительный кредит (предоплата или бонус)
    """
    if account_balance is None:
        return "unknown"
    try:
        val = float(account_balance)
    except (TypeError, ValueError):
        return "unknown"
    return "ok" if val >= 0 else "debt"


async def fetch_account(label: str, token: str) -> dict:
    """
    Возвращает сводку по одному аккаунту:
      {
        "label": str,
        "ok": bool,               # удалось ли опросить /balance
        "balance_status": str,    # "ok" | "debt" | "unknown"
        "month_to_date": str | None,
        "account_balance": str | None,
        "error": str | None,
      }
    Ошибки не пробрасываются — один мёртвый токен не роняет весь дайджест.
    """
    result: dict = {
        "label": label,
        "ok": False,
        "balance_status": "unknown",
        "month_to_date": None,
        "account_balance": None,
        "error": None,
    }
    try:
        balance = await _get_json("/customers/my/balance", token)
        result["month_to_date"] = balance.get("month_to_date_balance")
        result["account_balance"] = balance.get("account_balance")
        result["balance_status"] = _balance_status(result["account_balance"])
        result["ok"] = True
    except urllib.error.HTTPError as e:
        msg = {
            401: "неверный или отозванный токен (401)",
            403: "нет прав на биллинг (403) — проверьте scope токена",
            429: "превышен лимит запросов (429)",
        }.get(e.code, f"HTTP {e.code}")
        result["error"] = msg
        logger.warning("Биллинг DO: %s — %s", label, msg)
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
    """DO отдаёт суммы строками вида '-152.34'. Форматируем в $152.34."""
    if value is None:
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _balance_icon(balance_status: str) -> str:
    return {"ok": "🟢", "debt": "🔴", "unknown": "❓"}.get(balance_status, "❓")


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

        icon = _balance_icon(acc["balance_status"])
        ab = float(acc["account_balance"] or "0")
        if acc["balance_status"] == "debt":
            status_txt = f"задолженность {_money(acc['account_balance'])}"
        elif ab > 0:
            status_txt = f"кредит {_money(acc['account_balance'])}"
        else:
            status_txt = "задолженностей нет"

        lines.append(
            f"{icon} <b>{label}</b>\n"
            f"   💸 Потрачено за месяц: <b>{_money(acc['month_to_date'])}</b>\n"
            f"   📊 Баланс: {status_txt}"
        )
    return "\n".join(lines)


def has_problem(accounts: list[dict]) -> bool:
    """True, если хотя бы один аккаунт не опросился или имеет задолженность."""
    for acc in accounts:
        if not acc["ok"] or acc["balance_status"] == "debt":
            return True
    return False