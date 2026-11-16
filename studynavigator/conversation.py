"""Fixed replies for complete social utterances, never for project questions.

Only this controller may label a reply as conversation. The model's answer
schema intentionally does not offer that category as a grounding exemption.
"""

import re


GREETINGS = ("привет", "приветик", "приветствую", "здравствуй", "здравствуйте",
             "доброе утро", "добрый день", "добрый вечер", "hello", "hi", "hey")
THANKS = ("спасибо", "спасибо большое", "большое спасибо", "спасибо за помощь",
          "спасибо тебе", "благодарю", "thanks", "thank you")
CAPABILITIES = ("что ты умеешь", "что умеешь", "что ты можешь", "чем можешь помочь",
                "чем ты можешь помочь", "как ты можешь помочь", "как пользоваться сервисом")
HELP = ("Могу помочь разобраться в требованиях проекта, составить план с учётом доступного времени "
        "и обсудить прогресс. Предложенные изменения действий сохраняются после нажатия «Применить». "
        "Напишите, что хотите разобрать.")


def troubleshooting_reply(message: str, history=None) -> str | None:
    """Explain the immediately preceding recorded failure without a model call."""
    text = re.sub(r"\s+", " ", message.casefold()).strip(" \t\r\n.!?…")
    if not re.fullmatch(
        r"(?:почему|отчего)\s+(?:(?:оно|это|изменение|предложение)\s+)?"
        r"(?:не\s+(?:проходит|прошло)\s+проверку|(?:возникла|произошла)\s+ошибка)|"
        r"что\s+(?:значит|означает)\s+(?:эта\s+)?ошибка", text
    ):
        return None
    last = list(history or [])[-1:]
    if not last or not isinstance(last[0], dict) or last[0].get("role") != "assistant":
        return None
    failure = last[0]
    code = failure.get("error_code")
    if not code:
        code = {
            "Предложение не прошло проверку. Укажите действие и существующие задачи явно.": "invalid_action_proposal",
            "Данные прогресса отделены от источников материалов.": "invalid_project_context",
        }.get(failure.get("content"))
    if code == "invalid_action_proposal":
        return ("Не удалось проверить предложенное изменение действий: запрос или ответ ассистента "
                "не удалось однозначно сопоставить с текущим планом. Изменения не сохранены. "
                "Укажите действие и новое время, например: «Поменяй каждый блок на 240 минут». "
                "После проверки появится предложение с кнопкой «Применить».")
    if code == "invalid_project_context":
        return ("Ассистент смешал данные плана и ссылки на материалы, поэтому ответ не прошёл проверку. "
                "Это ошибка формирования ответа. Изменения не сохранены. Повторите запрос о плане.")
    return None


def conversational_reply(message: str) -> str | None:
    # Normalize only spacing and harmless surrounding punctuation. Never strip
    # arbitrary instructions, quoted text or the substantive part of a message.
    text = re.sub(r"\s+", " ", message.casefold()).strip(" \t\r\n.!?…👋🙂😊")
    if text in GREETINGS:
        return "Привет! Помогу разобраться с проектом. Что хотите обсудить: требования, план или следующий шаг?"
    if text in THANKS:
        return "Пожалуйста! Если понадобится помощь с проектом, напишите."
    if text in ("как дела", "как ты"):
        return "Готов помочь с проектом. Что хотите разобрать?"
    if text in CAPABILITIES:
        return HELP
    # A greeting plus a complete capabilities question is still social dialogue.
    for greeting in GREETINGS:
        if any(text == greeting + separator + question
               for separator in (" ", ", ", "! ", ". ") for question in CAPABILITIES):
            return "Привет! " + HELP
    return None
