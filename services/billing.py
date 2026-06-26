"""
Сервис мониторинга биллинга DigitalOcean.

Использует два эндпоинта (оба доступны через Organization/API токен):
  - /v2/customers/my/balance        — текущий баланс и расход за месяц
  - /v2/customers/my/billing_history — история платежей и инвойсов

Эндпоинт /v2/account не используется — требует отдельного scope account:read,
который при Organization-токенах часто недоступен (403).

Статус аккаунта определяем по account_balance (отрицательный = долг).
«Дату последнего платежа» и «дней без оплаты» берём из billing_history.

Документация: https://docs.digitalocean.com/reference/api/reference/billing/
Лимиты DO: 5000 req/час, 250 req/мин — для нескольких аккаунтов раз в сутки
запас огромный.
"""
import json
import asyncio
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone

from config import DO_ACCOUNTS

logger = logging.getLogger(__name__)

API_BASE = "https://api.digitalocean.com/v2"
HTTP_TIMEOUT = 15  # сек на запрос


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
    Определяет статус по балансу аккаунта.
      "0.00"   → "ok"
      "-45.20" → "debt"   (долг, списание не прошло)
      "10.00"  → "ok"     (положительный кредит)
    """
    if account_balance is None:
        return "unknown"
    try:
        return "ok" if float(account_balance) >= 0 else "debt"
    except (TypeError, ValueError):
        return "unknown"


def _last_payment_from_history(billing_history: list[dict]) -> dict | None:
    """
    Ищет самый свежий платёж в billing_history.

    DO возвращает платежи с type='Payment' и ОТРИЦАТЕЛЬНОЙ суммой
    (списание с карты = отток денег со счёта клиента в пользу DO).
    Например: amount="-327.55" означает что списали $327.55.

    Возвращает словарь вида:
      {"date": datetime, "amount": str, "description": str}
    или None, если платежей нет.
    """
    latest: dict | None = None
    latest_dt: datetime | None = None

    for entry in billing_history:
        if entry.get("type") != "Payment":
            continue

        date_str = entry.get("date", "")
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            # Показываем абсолютную сумму (без минуса) — для читаемости
            raw_amount = entry.get("amount", "0")
            try:
                display_amount = str(abs(float(raw_amount)))
            except (TypeError, ValueError):
                display_amount = raw_amount

            latest = {
                "date": dt,
                "amount": display_amount,
                "description": entry.get("description", ""),
            }

    return latest


async def fetch_account(label: str, token: str) -> dict:
    """
    Возвращает полную сводку по одному аккаунту:
      {
        "label": str,
        "ok": bool,
        "balance_status": str,        # "ok" | "debt" | "unknown"
        "month_to_date": str | None,
        "account_balance": str | None,
        "last_payment_date": datetime | None,
        "last_payment_amount": str | None,
        "days_since_payment": int | None,
        "history_error": str | None,  # ошибка billing_history (не фатальная)
        "error": str | None,          # ошибка /balance (фатальная для записи)
      }
    """
    result: dict = {
        "label": label,
        "ok": False,
        "balance_status": "unknown",
        "month_to_date": None,
        "account_balance": None,
        "last_payment_date": None,
        "last_payment_amount": None,
        "days_since_payment": None,
        "history_error": None,
        "error": None,
    }

    # --- /balance (обязательный запрос) ---
    try:
        balance = await _get_json("/customers/my/balance", token)
        result["month_to_date"] = balance.get("month_to_date_balance")
        result["account_balance"] = balance.get("account_balance")
        result["balance_status"] = _balance_status(result["account_balance"])
        result["ok"] = True
    except urllib.error.HTTPError as e:
        result["error"] = {
            401: "неверный или отозванный токен (401)",
            403: "нет прав на биллинг (403) — проверьте scope токена",
            429: "превышен лимит запросов (429)",
        }.get(e.code, f"HTTP {e.code}")
        logger.warning("Биллинг DO [balance]: %s — %s", label, result["error"])
        return result  # дальше нет смысла, токен не работает
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Биллинг DO [balance]: %s — %s", label, e)
        return result

    # --- /billing_history (необязательный — ошибка не роняет запись) ---
    try:
        history_data = await _get_json("/customers/my/billing_history", token)
        entries = history_data.get("billing_history", [])
        last = _last_payment_from_history(entries)
        if last:
            result["last_payment_date"] = last["date"]
            result["last_payment_amount"] = last["amount"]
            now = datetime.now(timezone.utc)
            result["days_since_payment"] = (now - last["date"]).days
    except urllib.error.HTTPError as e:
        result["history_error"] = f"история недоступна (HTTP {e.code})"
        logger.warning("Биллинг DO [history]: %s — HTTP %s", label, e.code)
    except Exception as e:
        result["history_error"] = str(e)
        logger.warning("Биллинг DO [history]: %s — %s", label, e)

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


def _fmt_date(dt: datetime | None) -> str:
    """Форматирует дату платежа в читаемый вид."""
    if dt is None:
        return "неизвестно"
    return dt.strftime("%-d %b %Y").lower()  # "1 jun 2026"


def format_digest(accounts: list[dict]) -> str:
    """Человекочитаемая сводка по всем аккаунтам для Telegram (HTML)."""
    if not accounts:
        return "⚠️ Аккаунты DigitalOcean не настроены (DO_ACCOUNTS в .env пуст)."

    blocks = []
    for acc in accounts:
        label = acc["label"]
        if not acc["ok"]:
            blocks.append(f"❓ <b>{label}</b>\nОшибка опроса: {acc['error']}")
            continue

        icon = _balance_icon(acc["balance_status"])
        ab = float(acc["account_balance"] or "0")
        if acc["balance_status"] == "debt":
            balance_txt = f"задолженность <b>{_money(acc['account_balance'])}</b>"
        elif ab > 0:
            balance_txt = f"кредит {_money(acc['account_balance'])}"
        else:
            balance_txt = "задолженностей нет"

        block = (
            f"{icon} <b>{label}</b>\n"
            f"   💸 Потрачено за месяц: <b>{_money(acc['month_to_date'])}</b>\n"
            f"   📊 Баланс: {balance_txt}"
        )

        if acc["last_payment_date"]:
            days = acc["days_since_payment"]
            paid_date = _fmt_date(acc["last_payment_date"])
            paid_amt = _money(acc["last_payment_amount"])
            block += f"\n   📅 Последний платёж: {paid_amt} — {paid_date}"
            if acc["balance_status"] == "debt":
                block += f" (<b>{days} дн. назад</b>)"
                block += "\n   ⚠️ Оплатите задолженность — серверы могут быть остановлены"
            else:
                now = datetime.now(timezone.utc)
                if now.month == 12:
                    next_bill = now.replace(year=now.year + 1, month=1, day=1,
                                            hour=0, minute=0, second=0, microsecond=0)
                else:
                    next_bill = now.replace(month=now.month + 1, day=1,
                                            hour=0, minute=0, second=0, microsecond=0)
                days_until = (next_bill - now).days
                next_bill_str = _fmt_date(next_bill)
                block += f"\n   🗓 Следующий счёт: ~{next_bill_str} (через {days_until} дн.)"
        elif acc["history_error"]:
            block += f"\n   📅 История: {acc['history_error']}"

        blocks.append(block)

    return "💳 <b>Биллинг DigitalOcean</b>\n\n" + "\n\n".join(blocks)


def has_problem(accounts: list[dict]) -> bool:
    """True, если хотя бы один аккаунт не опросился или имеет задолженность."""
    for acc in accounts:
        if not acc["ok"] or acc["balance_status"] == "debt":
            return True
    return False