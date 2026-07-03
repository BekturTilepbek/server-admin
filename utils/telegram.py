"""
Утилиты форматирования под лимиты Telegram.

Telegram режет сообщения на 4096 символов (MESSAGE_TOO_LONG при превышении).
Берём запас (SAFE_CHUNK_SIZE) под HTML-разметку и режем по границам блоков
(разделитель "\n\n" между отчётами по серверам), чтобы не разорвать
<b>...</b> / <pre>...</pre> посередине и не сломать парсинг HTML.
"""

TELEGRAM_MSG_LIMIT = 4096
SAFE_CHUNK_SIZE = 3500


def chunk_report(text: str, sep: str = "\n\n", limit: int = SAFE_CHUNK_SIZE) -> list[str]:
    """
    Разбивает длинный отчёт на части <= limit символов.

    Сначала пытается резать по границам блоков (sep), чтобы каждый блок
    (например, отчёт по одному серверу) остался целым и HTML-теги внутри
    него не разорвались. Если единичный блок сам по себе больше limit
    (крайне маловероятно, но на всякий случай) — режет его жёстко по длине.
    """
    if not text:
        return []

    parts = text.split(sep)
    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = f"{current}{sep}{part}" if current else part

        if len(candidate) <= limit:
            current = candidate
            continue

        # candidate не помещается — закрываем текущий чанк
        if current:
            chunks.append(current)
            current = ""

        if len(part) > limit:
            # Один блок сам по себе больше лимита — режем жёстко
            for i in range(0, len(part), limit):
                chunks.append(part[i:i + limit])
        else:
            current = part

    if current:
        chunks.append(current)

    return chunks