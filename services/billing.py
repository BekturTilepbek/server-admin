"""
Сервис мониторинга биллинга DigitalOcean.

Использует два эндпоинта (доступны через Organization/API токен):
  - /v2/customers/my/balance         — баланс, расход месяца, кэшированные суммы
  - /v2/customers/my/billing_history — история платежей и инвойсов

Эндпоинт /v2/account не используется — требует отдельного scope account:read,
который при Organization-токенах часто недоступен (403).

ВАЖНО про поля /balance (подтверждено официальной документацией DO):
  - account_balance:      текущий общий баланс.
      ОТРИЦАТЕЛЬНЫЙ = у вас ЕСТЬ ЗАПАС/КРЕДИТ — это ОК.
      ПОЛОЖИТЕЛЬНЫЙ = вы ДОЛЖНЫ эту сумму — риск suspend.
      (см. https://www.digitalocean.com/community/questions/where-can-i-see-my-account-balance)
  - month_to_date_balance = account_balance + month_to_date_usage.
      Это СУММА долга и расхода текущего месяца — НЕЛЬЗЯ показывать как
      "расход за месяц", иначе неоплаченный прошлый инвойс визуально
      складывается с текущим расходом и создаёт путаницу.
      (см. doctl balance get: "Your month-to-date balance including your
      account balance and month-to-date usage.")
  - month_to_date_usage:  ЧИСТЫЙ расход текущего (ещё не выставленного)
      месяца — вот что нужно показывать как "Расход в этом месяце".

Логика suspend (по наблюдениям владельца аккаунтов):
  1) Не оплачен инвойс за прошлый месяц (выставляется 1-го числа) -> DO даёт
     отсрочку, но suspend обычно наступает с начала 3-й календарной недели
     месяца, в котором выставлен инвойс. "Неделя" считается по календарю
     (понедельник-воскресенье), поэтому дата "начала 3-й недели" гуляет
     от 9 до 15 числа в зависимости от того, на какой день недели пришлось
     1-е число — вычисляем через calendar.monthcalendar, не хардкодим день.
  2) Превышение внутренних лимитов DO (обычно на новых аккаунтах) -> suspend
     может быть мгновенным. Причину API не отдаёт вообще — предсказать
     нельзя, только видно постфактум.

Все даты в сообщениях — по времени Бишкека (GMT+6) и на русском языке,
согласно конвенции проекта (см. utils/scheduler.py: TZ_GMT6).
"""
import json
import asyncio
import logging
import calendar
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from config import DO_ACCOUNTS

logger = logging.getLogger(__name__)

API_BASE = "https://api.digitalocean.com/v2"
HTTP_TIMEOUT = 15  # сек на запрос

TZ_GMT6 = timezone(timedelta(hours=6))

RU_MONTHS_NOM = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
RU_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


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
    Определяет статус по общему балансу аккаунта.

    ВНИМАНИЕ, знак обратный интуитивному:
      - ОТРИЦАТЕЛЬНЫЙ ("-61.68") = есть запас/кредит -> "ok"
      - ПОЛОЖИТЕЛЬНЫЙ ("61.68")  = вы должны эту сумму -> "debt"
      - "0.00" -> "ok"
    """
    if account_balance is None:
        return "unknown"
    try:
        return "debt" if float(account_balance) > 0 else "ok"
    except (TypeError, ValueError):
        return "unknown"


def _parse_iso(date_str: str) -> datetime | None:
    """Парсит дату DO ('2026-06-01T00:00:00Z') в aware datetime (UTC)."""
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _to_local(dt: datetime | None) -> datetime | None:
    """Конвертирует aware datetime в часовой пояс Бишкека (GMT+6)."""
    if dt is None:
        return None
    return dt.astimezone(TZ_GMT6)


def _last_entry_by_type(billing_history: list[dict], entry_type: str) -> dict | None:
    """
    Ищет самую свежую запись заданного типа ('Payment' или 'Invoice')
    в billing_history. Возвращает {"date": datetime (UTC), "amount": str}.
    """
    latest: dict | None = None
    latest_dt: datetime | None = None

    for entry in billing_history:
        if entry.get("type") != entry_type:
            continue
        dt = _parse_iso(entry.get("date", ""))
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest = {"date": dt, "amount": entry.get("amount", "0")}

    return latest


def _last_payment_from_history(billing_history: list[dict]) -> dict | None:
    """
    Ищет самый свежий платёж. DO возвращает платежи с ОТРИЦАТЕЛЬНОЙ суммой
    (списание с карты). Возвращает сумму по модулю — для читаемости.
    """
    last = _last_entry_by_type(billing_history, "Payment")
    if not last:
        return None
    try:
        last["amount"] = str(abs(float(last["amount"])))
    except (TypeError, ValueError):
        pass
    return last


def _last_invoice_from_history(billing_history: list[dict]) -> dict | None:
    """Ищет самый свежий инвойс (type='Invoice'). Сумма — как есть (положительная)."""
    return _last_entry_by_type(billing_history, "Invoice")


async def fetch_account(label: str, token: str) -> dict:
    """
    Возвращает полную сводку по одному аккаунту:
      {
        "label": str,
        "ok": bool,
        "balance_status": str,           # "ok" | "debt" | "unknown"
        "account_balance": str | None,   # общий баланс (см. знак выше)
        "month_to_date_usage": str | None,   # ЧИСТЫЙ расход текущего месяца
        "last_payment_date": datetime | None,   # UTC-aware
        "last_payment_amount": str | None,
        "last_invoice_date": datetime | None,   # UTC-aware
        "last_invoice_amount": str | None,
        "history_error": str | None,     # ошибка billing_history (не фатальная)
        "error": str | None,             # ошибка /balance (фатальная для записи)
      }
    """
    result: dict = {
        "label": label,
        "ok": False,
        "balance_status": "unknown",
        "account_balance": None,
        "month_to_date_usage": None,
        "last_payment_date": None,
        "last_payment_amount": None,
        "last_invoice_date": None,
        "last_invoice_amount": None,
        "history_error": None,
        "error": None,
    }

    # --- /balance (обязательный запрос) ---
    try:
        balance = await _get_json("/customers/my/balance", token)
        result["account_balance"] = balance.get("account_balance")
        # ВАЖНО: берём именно month_to_date_usage, а НЕ month_to_date_balance —
        # последнее уже включает в себя account_balance (см. docstring модуля)
        # и покажет завышенную сумму, слив долг и расход месяца воедино.
        result["month_to_date_usage"] = balance.get("month_to_date_usage")
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

        last_pay = _last_payment_from_history(entries)
        if last_pay:
            result["last_payment_date"] = last_pay["date"]
            result["last_payment_amount"] = last_pay["amount"]

        last_inv = _last_invoice_from_history(entries)
        if last_inv:
            result["last_invoice_date"] = last_inv["date"]
            result["last_invoice_amount"] = last_inv["amount"]
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


# ------------------- ФОРМАТИРОВАНИЕ -------------------

def _money(value: str | None) -> str:
    """DO отдаёт суммы строками. Форматируем в $152.34."""
    if value is None:
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _balance_icon(balance_status: str) -> str:
    return {"ok": "🟢", "debt": "🔴", "unknown": "❓"}.get(balance_status, "❓")


def format_ru_date(dt: datetime | None) -> str:
    """Форматирует дату на русском в часовом поясе Бишкека: '15 июля 2026'."""
    if dt is None:
        return "неизвестно"
    local = _to_local(dt)
    return f"{local.day} {RU_MONTHS_GEN[local.month - 1]} {local.year}"


def _invoice_period_name(invoice_dt: datetime) -> str:
    """
    Инвойс выставляется 1-го числа за ПРОШЕДШИЙ месяц использования.
    Возвращает название этого месяца в именительном падеже: 'июнь'.
    """
    local = _to_local(invoice_dt)
    month = local.month - 1
    if month == 0:
        month = 12
    return RU_MONTHS_NOM[month - 1]


def _week_boundaries(year: int, month: int) -> tuple[int, int]:
    """
    Возвращает (день_начала_2й_недели, день_начала_3й_недели) месяца,
    используя настоящий календарь (недели Пн-Вс), а не фиксированное число.

    ВАЖНО: DigitalOcean официально НЕ публикует точные сроки перехода
    past due -> suspended:
    "How fast an account moves between past due, suspension, and permanent
    deletion depends on the account. DigitalOcean does not publish fixed
    timelines for these stages."
    (https://docs.digitalocean.com/platform/billing/late-payments/)

    Поэтому мы НЕ считаем точную дату, а даём ОЦЕНОЧНЫЙ диапазон на основе
    наблюдений владельца аккаунтов (обычно 2-3 неделя месяца).
    """
    weeks = calendar.monthcalendar(year, month)  # список недель, Пн — первый день
    # Начиная со 2-й строки (индекс 1) все недели полные и гарантированно
    # существуют в пределах месяца (в любом месяце минимум 4 полных недели).
    week2_start = weeks[1][0]
    week3_start = weeks[2][0]
    return week2_start, week3_start


def _invoice_deadline_range(invoice_dt: datetime) -> tuple[datetime, datetime]:
    """
    Оценочный диапазон [начало 2-й недели, начало 3-й недели] месяца,
    в котором выставлен инвойс (по времени Бишкека). Это ОЦЕНКА по
    наблюдениям, а не гарантированная дата — DO не публикует точные сроки.
    """
    local = _to_local(invoice_dt)
    d2, d3 = _week_boundaries(local.year, local.month)
    start = local.replace(day=d2, hour=0, minute=0, second=0, microsecond=0)
    end = local.replace(day=d3, hour=0, minute=0, second=0, microsecond=0)
    return start, end

def format_account_block(acc: dict) -> str:
    """
    Рендерит блок по одному аккаунту. Используется и в ежедневном дайджесте,
    и в мгновенном алерте — чтобы формулировки не расходились между собой.
    Каждый блок заканчивается короткой статусной строкой.
    """
    label = acc["label"]

    if not acc["ok"]:
        return (
            f"❓ <b>{label}</b>\n"
            f"Ошибка опроса: {acc['error']}\n"
            f"   📌 Статус: не удалось проверить — проверьте токен вручную"
        )

    if acc["balance_status"] == "unknown":
        return (
            f"❓ <b>{label}</b>\n"
            f"Не удалось определить статус баланса (некорректные данные API).\n"
            f"   📌 Статус: не удалось проверить — посмотрите вручную в панели DO"
        )

    icon = _balance_icon(acc["balance_status"])

    if acc["balance_status"] == "debt":
        invoice_amount = acc["last_invoice_amount"] or acc["account_balance"]
        block = f"{icon} <b>{label}</b> — ТРЕБУЕТ ОПЛАТЫ\n"

        deadline_state = "before"  # before | within | after
        if acc["last_invoice_date"]:
            period = _invoice_period_name(acc["last_invoice_date"])
            block += (
                f"   ⚠️ Не оплачен счёт за {period}: <b>{_money(invoice_amount)}</b> "
                f"(выставлен {format_ru_date(acc['last_invoice_date'])})\n"
            )
            start, end = _invoice_deadline_range(acc["last_invoice_date"])
            now_local = datetime.now(TZ_GMT6)
            if now_local > end:
                deadline_state = "after"
                block += (
                    f"   ⏰ Ориентировочное окно ({format_ru_date(start)}–{format_ru_date(end)}) "
                    f"уже прошло — DO не публикует точные сроки, проверьте вручную, "
                    f"не заблокирован ли аккаунт\n"
                )
            elif now_local >= start:
                deadline_state = "within"
                block += (
                    f"   ⏰ Вы сейчас в ориентировочной зоне риска "
                    f"({format_ru_date(start)}–{format_ru_date(end)}) — "
                    f"оплатите как можно скорее (точных сроков DO не публикует)\n"
                )
            else:
                block += (
                    f"   ⏰ Ориентировочно оплатите до {format_ru_date(start)}–{format_ru_date(end)} "
                    f"(точных сроков DO не публикует), иначе риск блокировки\n"
                )
        else:
            block += f"   ⚠️ Не оплачен счёт: <b>{_money(invoice_amount)}</b>\n"
            block += "   ⏰ Оплатите как можно скорее, иначе аккаунт может быть заблокирован\n"

        block += f"   💸 Расход в этом месяце (ещё не выставлен): {_money(acc['month_to_date_usage'])}\n"

        if deadline_state == "after":
            block += "   📌 Статус: ⚠️ Просрочено! Оплатите немедленно и проверьте аккаунт"
        elif deadline_state == "within":
            block += "   📌 Статус: ⚠️ Риск блокировки — оплатите как можно скорее"
        else:
            block += "   📌 Статус: ⚠️ Надо оплатить — не забудьте внести платёж"
        return block

    # balance_status == "ok"
    block = f"{icon} <b>{label}</b>\n"
    try:
        ab = float(acc["account_balance"] or "0")
    except (TypeError, ValueError):
        ab = 0.0

    if ab < 0:
        block += f"   ✅ Уже оплачено (остаток на счету: {_money(str(abs(ab)))})\n"
    else:
        block += "   ✅ Уже оплачено\n"

    if acc["last_payment_date"] and acc["last_payment_amount"]:
        block += (
            f"   📅 Последний платёж: {_money(acc['last_payment_amount'])} "
            f"({format_ru_date(acc['last_payment_date'])})\n"
        )
    block += f"   💸 Расход в этом месяце: <b>{_money(acc['month_to_date_usage'])}</b>\n"
    block += "   📌 Статус: ✅ Всё хорошо, всё оплачено"
    return block


def format_digest(accounts: list[dict]) -> str:
    """Полная сводка по всем аккаунтам DO для Telegram (HTML)."""
    if not accounts:
        return "⚠️ Аккаунты DigitalOcean не настроены (DO_ACCOUNTS в .env пуст)."

    blocks = [format_account_block(acc) for acc in accounts]
    return "💳 <b>Биллинг DigitalOcean</b>\n\n" + "\n\n".join(blocks)


def has_problem(accounts: list[dict]) -> bool:
    """True, если хотя бы один аккаунт не опросился, в долгу, либо статус неясен."""
    for acc in accounts:
        if not acc["ok"] or acc["balance_status"] in ("debt", "unknown"):
            return True
    return False


# ------------------- ИСТОРИЯ БИЛЛИНГА (инвойсы + платежи) -------------------

async def fetch_billing_history(label: str, token: str) -> dict:
    """
    Тянет сырую историю биллинга по аккаунту.
    Возвращает {"label", "ok", "entries"} или {"label", "ok": False, "error"}.
    """
    try:
        data = await _get_json("/customers/my/billing_history", token)
        return {"label": label, "ok": True, "entries": data.get("billing_history", [])}
    except urllib.error.HTTPError as e:
        return {"label": label, "ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"label": label, "ok": False, "error": str(e)}


def format_history(label: str, entries: list[dict], limit: int = 6) -> str:
    """
    Читаемая история для Telegram: отдельно инвойсы, отдельно платежи,
    по limit последних записей каждого типа (свежие сверху), даты на русском.
    """
    if not entries:
        return f"📜 <b>{label}</b>\nИстория пуста или недоступна."

    invoices: list[tuple[datetime | None, str]] = []
    payments: list[tuple[datetime | None, str]] = []
    for e in entries:
        dt = _parse_iso(e.get("date", ""))
        amount = e.get("amount", "0")
        t = e.get("type")
        if t == "Invoice":
            invoices.append((dt, amount))
        elif t == "Payment":
            payments.append((dt, amount))

    key = lambda row: row[0] or datetime.min.replace(tzinfo=timezone.utc)
    invoices.sort(key=key, reverse=True)
    payments.sort(key=key, reverse=True)

    lines = [f"📜 <b>История биллинга — {label}</b>"]

    lines.append("\n🧾 <b>Инвойсы</b> (начислено):")
    if invoices:
        for dt, amount in invoices[:limit]:
            lines.append(f"   • {format_ru_date(dt)} — {_money(amount)}")
    else:
        lines.append("   — нет")

    lines.append("\n💳 <b>Платежи</b> (оплачено):")
    if payments:
        for dt, amount in payments[:limit]:
            try:
                shown = _money(str(abs(float(amount))))
            except (TypeError, ValueError):
                shown = _money(amount)
            lines.append(f"   • {format_ru_date(dt)} — {shown}")
    else:
        lines.append("   — нет")

    return "\n".join(lines)