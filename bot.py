import json
import logging
import os
import random
import re
from datetime import datetime, timedelta

from telegram import Message, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _fix_mojibake(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    try:
        # Fix strings like "РџСЂРёРІРµС‚" -> "Привет"
        candidate = text.encode("cp1251").decode("utf-8")
    except Exception:
        return text

    def score(s: str) -> int:
        return s.count("Р") + s.count("С") + s.count("Ѓ") + s.count("Џ")

    return candidate if score(candidate) < score(text) else text


_orig_reply_text = Message.reply_text


async def _reply_text_fixed(self, text, *args, **kwargs):
    if isinstance(text, str):
        text = _fix_mojibake(text)
    return await _orig_reply_text(self, text, *args, **kwargs)


Message.reply_text = _reply_text_fixed

DATA_FILE = "data.json"
DUEL_STATS_FILE = "duel_stats.json"
WAR_STATS_FILE = "war_stats.json"
WORD_GAME_FILE = "word_game.json"
RAID_FILE = "raid_state.json"
DAILY_FILE = "daily_rewards.json"
INVENTORY_FILE = "inventory.json"
MOPS_FILE = "mops_helper.json"

SHOP_ITEMS = {
    "sword": {"name": "Железный меч", "price": 120},
    "shield": {"name": "Щит стража", "price": 110},
    "potion": {"name": "Зелье лечения", "price": 60},
    "bomb": {"name": "Бомба", "price": 95},
    "amulet": {"name": "Амулет удачи", "price": 180},
    "ring": {"name": "Обручальное кольцо", "price": 150},
}

RING_CHOICES = {
    "ring_12k": {"name": "Кольцо 12 карат", "price": 120},
    "ring_18k": {"name": "Кольцо 18 карат", "price": 220},
    "ring_24k": {"name": "Кольцо 24 карат", "price": 350},
}

SHOP_ITEMS.update(RING_CHOICES)

marriages: dict[str, list[dict]] = {}
duel_stats: dict[str, dict[str, int]] = {}
war_stats: dict[str, dict[str, int]] = {}
duel_requests: dict[str, dict[str, str]] = {}
active_duels: dict[str, dict[str, str]] = {}
pending_marriages: dict[str, dict[str, str]] = {}
word_games: dict[str, dict] = {}
raid_states: dict[str, dict] = {}
daily_rewards: dict[str, dict] = {}
inventories: dict[str, dict[str, int]] = {}
mops_state: dict[str, dict] = {}
BOT_STARTED_AT = datetime.now()


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as e:
        logger.warning("Cannot read %s: %s", path, e)
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_mentioned_users(text: str | None) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(re.findall(r"@(\w+)", text)))


def ensure_stat_user(stats: dict[str, dict[str, int]], user: str) -> None:
    if user not in stats:
        stats[user] = {"wins": 0, "losses": 0, "draws": 0}


def duel_key(chat_id: str, challenger: str, target: str) -> str:
    return f"{chat_id}:{challenger}:{target}"


def find_request_for_target(chat_id: str, target: str) -> tuple[str, dict[str, str]] | tuple[None, None]:
    prefix = f"{chat_id}:"
    for key, req in duel_requests.items():
        if key.startswith(prefix) and req.get("target") == target:
            return key, req
    return None, None


def clear_duel_requests_for_pair(chat_id: str, user_a: str, user_b: str) -> None:
    keys = [
        duel_key(chat_id, user_a, user_b),
        duel_key(chat_id, user_b, user_a),
    ]
    for k in keys:
        duel_requests.pop(k, None)


def marriage_key(chat_id: str, user_a: str, user_b: str) -> str:
    first, second = sorted([user_a, user_b])
    return f"{chat_id}:{first}:{second}"


def norm_user(user: str | None) -> str:
    return (user or "").strip().lower()


def is_user_in_marriage(chat_id: str, user: str) -> bool:
    needle = norm_user(user)
    for m in marriages.get(chat_id, []):
        members = [norm_user(x) for x in m.get("members", [])]
        if m.get("type") == "marriage" and needle in members:
            return True
    return False


def find_pending_marriage_for_user(chat_id: str, user: str) -> tuple[str, dict[str, str]] | tuple[None, None]:
    prefix = f"{chat_id}:"
    user = norm_user(user)
    matched: list[tuple[str, dict[str, str]]] = []
    for key, data in pending_marriages.items():
        if not key.startswith(prefix):
            continue
        a = norm_user(data.get("a"))
        b = norm_user(data.get("b"))
        if user in (a, b):
            matched.append((key, data))

    if not matched:
        return None, None

    # If user has multiple pending proposals, choose the latest one.
    matched.sort(key=lambda item: float(item[1].get("created_at", "0")), reverse=True)
    return matched[0]


def clear_pending_marriages_for_users(chat_id: str, users: set[str]) -> None:
    users = {norm_user(u) for u in users}
    prefix = f"{chat_id}:"
    to_delete = []
    for key, data in pending_marriages.items():
        if not key.startswith(prefix):
            continue
        a = norm_user(data.get("a"))
        b = norm_user(data.get("b"))
        if a in users or b in users:
            to_delete.append(key)
    for key in to_delete:
        pending_marriages.pop(key, None)


def find_marriage_for_user(chat_id: str, user: str) -> tuple[int, dict] | tuple[None, None]:
    user = norm_user(user)
    records = marriages.get(chat_id, [])
    for idx, m in enumerate(records):
        members = [norm_user(x) for x in m.get("members", [])]
        if m.get("type") == "marriage" and user in members:
            return idx, m
    return None, None


def parse_iso_date(dt: str | None) -> datetime | None:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt)
    except Exception:
        return None


def resolve_ring_id(raw: str | None) -> str | None:
    token = (raw or "").strip().lower()
    aliases = {
        "12": "ring_12k",
        "12k": "ring_12k",
        "ring12": "ring_12k",
        "ring_12k": "ring_12k",
        "18": "ring_18k",
        "18k": "ring_18k",
        "ring18": "ring_18k",
        "ring_18k": "ring_18k",
        "24": "ring_24k",
        "24k": "ring_24k",
        "ring24": "ring_24k",
        "ring_24k": "ring_24k",
        "ring": "ring",
    }
    ring_id = aliases.get(token)
    if ring_id and ring_id in SHOP_ITEMS:
        return ring_id
    return None


def pick_best_common_ring(inv_a: dict[str, int], inv_b: dict[str, int]) -> str | None:
    priority = ["ring_24k", "ring_18k", "ring_12k", "ring"]
    for ring_id in priority:
        if int(inv_a.get(ring_id, 0)) > 0 and int(inv_b.get(ring_id, 0)) > 0:
            return ring_id
    return None


def total_rings(inv: dict[str, int]) -> int:
    return sum(
        int(qty)
        for item_id, qty in inv.items()
        if item_id == "ring" or item_id.startswith("ring_")
    )


def make_active_duel_key(chat_id: str, user_a: str, user_b: str) -> str:
    first, second = sorted([user_a, user_b])
    return f"{chat_id}:{first}:{second}"


def find_active_duel_for_user(chat_id: str, user: str) -> tuple[str, dict[str, str]] | tuple[None, None]:
    prefix = f"{chat_id}:"
    for key, duel in active_duels.items():
        if key.startswith(prefix) and user in (duel.get("a"), duel.get("b")):
            return key, duel
    return None, None


def normalize_word(word: str) -> str:
    return re.sub(r"[^a-zA-ZР°-СЏРђ-РЇС‘РЃ]", "", word).lower().replace("С‘", "Рµ")


def get_last_letter(word: str) -> str:
    skip = {"СЊ", "СЉ", "С‹"}
    for ch in reversed(word):
        if ch not in skip:
            return ch
    return ""


def get_user_key(username: str | None, user_id: int) -> str:
    return username or str(user_id)


def today_str() -> str:
    return datetime.now().date().isoformat()


def format_uptime(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}д {hours}ч {minutes}м"
    return f"{hours}ч {minutes}м"


def ensure_mops_chat(chat_id: str) -> dict:
    chats = mops_state.setdefault("chats", {})
    chat_cfg = chats.setdefault(chat_id, {"enabled": True, "last_sent": ""})
    return chat_cfg


def ensure_wallet(user: str) -> dict:
    return daily_rewards.setdefault(user, {"coins": 0, "streak": 0, "last_claim": ""})


def ensure_inventory(user: str) -> dict[str, int]:
    return inventories.setdefault(user, {})


def hp_bar(current_hp: int, max_hp: int = 100, width: int = 10) -> str:
    current_hp = max(0, min(max_hp, current_hp))
    filled = int(round((current_hp / max_hp) * width))
    return "❤️" * filled + "🖤" * (width - filled)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    ensure_mops_chat(chat_id)
    save_json(MOPS_FILE, mops_state)

    await update.message.reply_text(
        "Команды:\n"
        "/brak @user1 @user2 ... (2-67)\n"
        "/razvod [@user]\n"
        "/alyans @user1 @user2 ... (1-80)\n"
        "/vragi @user1 @user2\n"
        "/braki\n"
        "/soyuzy\n"
        "/moisoyuz\n\n"
        "/anniversary [@user]\n"
        "/rings\n"
        "/ring_exchange [12k|18k|24k]\n\n"
        "PvP:\n"
        "/duel @user или /pvp @user\n"
        "/accept\n"
        "/decline\n"
        "/shot\n"
        "/pvpstats [@user]\n"
        "/pvptop\n"
        "/duel_help\n\n"
        "Война:\n"
        "/war @user\n"
        "/warstats [@user]\n"
        "/wartop\n\n"
        "Игра в слова:\n"
        "/words_start\n"
        "/word слово\n"
        "/words_status\n"
        "/words_stop\n\n"
        "Рейд и экономика:\n"
        "/raid_start\n"
        "/raid_hit\n"
        "/raid_status\n"
        "/raid_top\n"
        "/raid_help\n"
        "/daily\n"
        "/balance [@user]\n"
        "/eco_help\n\n"
        "Магазин:\n"
        "/shop\n"
        "/buy item_id [кол-во]\n"
        "/inventory [@user]"
    )

async def brak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    proposer = norm_user(msg.from_user.username or str(msg.from_user.id))
    mentioned = get_mentioned_users(msg.text)

    if len(mentioned) != 1:
        await msg.reply_text("Использование: /brak @user")
        return

    target = norm_user(mentioned[0])
    if target == proposer:
        await msg.reply_text("Нельзя жениться на себе")
        return

    if is_user_in_marriage(chat_id, proposer) or is_user_in_marriage(chat_id, target):
        await msg.reply_text("Один из участников уже в браке")
        return

    inv_a = ensure_inventory(proposer)
    inv_b = ensure_inventory(target)
    if total_rings(inv_a) < 1 or total_rings(inv_b) < 1:
        await msg.reply_text(
            "Без колец брак заключить нельзя.\n"
            "У каждого участника должно быть хотя бы одно кольцо.\n"
            "Список колец: /rings\n"
            "Покупка: /buy ring_12k (или ring_18k / ring_24k)"
        )
        return

    # Clear stale/parallel requests for either participant to avoid wrong pairing.
    clear_pending_marriages_for_users(chat_id, {proposer, target})

    key = marriage_key(chat_id, proposer, target)
    if key in pending_marriages:
        await msg.reply_text("Приглашение на свадьбу уже отправлено")
        return

    pending_marriages[key] = {
        "chat_id": chat_id,
        "a": proposer,
        "b": target,
        "a_ok": "0",
        "b_ok": "0",
        "created_at": str(datetime.now().timestamp()),
    }
    await msg.reply_text(
        f"💌 @{proposer} зовет @{target} на свадьбу!\n\n"
        "Для церемонии оба участника должны написать в чат:\n"
        "согласен (или согласна)\n\n"
        "Если кто-то пишет: не согласен / не согласна — свадьба отменяется."
    )


async def marriage_ceremony_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.from_user and msg.from_user.is_bot:
        return

    text = (msg.text or "").strip().lower()
    chat_id = str(msg.chat_id)
    user = norm_user(msg.from_user.username or str(msg.from_user.id))

    key, request = find_pending_marriage_for_user(chat_id, user)
    if not request:
        return

    a = norm_user(request.get("a"))
    b = norm_user(request.get("b"))

    agree_words = {"согласен", "согласна"}
    decline_words = {"не согласен", "не согласна", "против", "отказываюсь"}

    if text in decline_words:
        pending_marriages.pop(key, None)
        await msg.reply_text(
            f"💔 Церемония отменена.\n"
            f"@{user} не согласен(на) на брак."
        )
        return

    if text not in agree_words:
        return

    if user == a:
        request["a_ok"] = "1"
    elif user == b:
        request["b_ok"] = "1"
    else:
        return

    pending_marriages[key] = request
    a_ok = request.get("a_ok") == "1"
    b_ok = request.get("b_ok") == "1"

    if not (a_ok and b_ok):
        waiting_for = b if user == a else a
        await msg.reply_text(
            f"✅ @{user} сказал(а): «согласен/согласна».\n"
            f"Ждем ответ от @{waiting_for}."
        )
        return

    # Оба согласились — создаем брак.
    if is_user_in_marriage(chat_id, a) or is_user_in_marriage(chat_id, b):
        pending_marriages.pop(key, None)
        await msg.reply_text("Свадьба отменена: один из участников уже в браке.")
        return

    marriages.setdefault(chat_id, [])
    marriages[chat_id].append(
        {
            "type": "marriage",
            "members": [a, b],
            "date": datetime.now().isoformat(),
            "wedding_date": datetime.now().isoformat(),
            "rings_exchanged": False,
            "rings_date": "",
        }
    )
    save_json(DATA_FILE, marriages)
    pending_marriages.pop(key, None)
    await msg.reply_text(
        "💍✨ Торжественная церемония завершена!\n\n"
        f"@{a} и @{b} теперь официально в браке!\n"
        "Пусть ваш союз будет крепким, счастливым и долгим! ❤️\n\n"
        "💍 Обменяйтесь кольцами:\n"
        "/ring_exchange 12k\n"
        "/ring_exchange 18k\n"
        "/ring_exchange 24k"
    )


async def razvod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    user = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)

    if chat_id not in marriages:
        await msg.reply_text("РќРµС‚ Р±СЂР°РєРѕРІ")
        return

    if mentioned:
        target = mentioned[0]
        for m in list(marriages[chat_id]):
            if m.get("type") == "marriage" and target in m.get("members", []):
                marriages[chat_id].remove(m)
                save_json(DATA_FILE, marriages)
                await msg.reply_text(f"@{target} СЂР°Р·РІРµРґРµРЅ(Р°)")
                return
        await msg.reply_text("РќРµ РЅР°Р№РґРµРЅРѕ")
        return

    found = False
    for m in list(marriages[chat_id]):
        if m.get("type") == "marriage" and user in m.get("members", []):
            marriages[chat_id].remove(m)
            found = True

    if found:
        save_json(DATA_FILE, marriages)
        await msg.reply_text("Р Р°Р·РІРѕРґ РІС‹РїРѕР»РЅРµРЅ")
    else:
        await msg.reply_text("РўС‹ РЅРµ РІ Р±СЂР°РєРµ")


async def anniversary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    caller = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)
    user = mentioned[0] if mentioned else caller

    _, marriage = find_marriage_for_user(chat_id, user)
    if not marriage:
        await msg.reply_text("Брак не найден.")
        return

    members = marriage.get("members", [])
    partner = members[1] if members and members[0] == user and len(members) > 1 else (members[0] if members else "")
    wedding_dt = parse_iso_date(marriage.get("wedding_date") or marriage.get("date"))
    if not wedding_dt:
        await msg.reply_text("Дата свадьбы не найдена.")
        return

    today = datetime.now()
    days_together = (today.date() - wedding_dt.date()).days
    next_anniv = wedding_dt.replace(year=today.year)
    if next_anniv.date() < today.date():
        next_anniv = next_anniv.replace(year=today.year + 1)
    days_to_anniv = (next_anniv.date() - today.date()).days
    rings_status = "да" if marriage.get("rings_exchanged") else "нет"
    ring_type = marriage.get("ring_type", "")
    ring_label = SHOP_ITEMS.get(ring_type, {"name": ring_type}).get("name", ring_type) if ring_type else "не выбран"

    await msg.reply_text(
        f"💞 Пара: @{user} + @{partner}\n"
        f"Дата свадьбы: {wedding_dt.strftime('%d.%m.%Y')}\n"
        f"Вместе дней: {days_together}\n"
        f"До годовщины: {days_to_anniv} дней\n"
        f"Обмен кольцами: {rings_status}\n"
        f"Тип колец: {ring_label}"
    )


async def ring_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    user = msg.from_user.username or str(msg.from_user.id)

    idx, marriage = find_marriage_for_user(chat_id, user)
    if marriage is None:
        await msg.reply_text("Ты не состоишь в браке.")
        return

    members = marriage.get("members", [])
    if len(members) != 2:
        await msg.reply_text("Обмен кольцами доступен только для пары.")
        return

    a, b = members[0], members[1]
    inv_a = ensure_inventory(a)
    inv_b = ensure_inventory(b)
    requested_ring_id = resolve_ring_id(context.args[0]) if context.args else None
    ring_id = requested_ring_id or pick_best_common_ring(inv_a, inv_b)
    if not ring_id:
        await msg.reply_text(
            "Для обмена кольцами у обоих должен быть одинаковый тип кольца.\n"
            "Список типов: /rings\n"
            "Покупка: /buy ring_12k или /buy ring_18k или /buy ring_24k"
        )
        return

    ring_a = int(inv_a.get(ring_id, 0))
    ring_b = int(inv_b.get(ring_id, 0))
    if ring_a < 1 or ring_b < 1:
        await msg.reply_text(
            f"Для обмена кольцами типа `{ring_id}` у каждого должно быть минимум 1 кольцо.\n"
            "Список: /rings"
        )
        return

    inv_a[ring_id] = ring_a - 1
    inv_b[ring_id] = ring_b - 1
    marriage["rings_exchanged"] = True
    marriage["rings_date"] = datetime.now().isoformat()
    marriage["ring_type"] = ring_id
    marriages[chat_id][idx] = marriage

    save_json(DATA_FILE, marriages)
    save_json(INVENTORY_FILE, inventories)
    ring_name = SHOP_ITEMS.get(ring_id, {"name": ring_id}).get("name", ring_id)
    await msg.reply_text(
        "💍💍 Обмен кольцами состоялся!\n"
        f"Тип колец: {ring_name}\n"
        f"Поздравляем @{a} и @{b} с еще более крепким союзом!"
    )


async def rings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [
        "Варианты колец:",
        f"- ring_12k: {SHOP_ITEMS['ring_12k']['name']} ({SHOP_ITEMS['ring_12k']['price']} монет)",
        f"- ring_18k: {SHOP_ITEMS['ring_18k']['name']} ({SHOP_ITEMS['ring_18k']['price']} монет)",
        f"- ring_24k: {SHOP_ITEMS['ring_24k']['name']} ({SHOP_ITEMS['ring_24k']['price']} монет)",
        "",
        "Покупка:",
        "/buy ring_12k",
        "/buy ring_18k",
        "/buy ring_24k",
        "",
        "Обмен:",
        "/ring_exchange 12k",
        "/ring_exchange 18k",
        "/ring_exchange 24k",
    ]
    await update.message.reply_text("\n".join(lines))


async def alyans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    mentioned = get_mentioned_users(msg.text)

    if len(mentioned) < 1 or len(mentioned) > 80:
        await msg.reply_text("РђР»СЊСЏРЅСЃ: 1-80 С‡РµР»РѕРІРµРє")
        return

    marriages.setdefault(chat_id, [])
    marriages[chat_id].append(
        {"type": "union", "members": mentioned, "date": datetime.now().isoformat()}
    )
    save_json(DATA_FILE, marriages)
    await msg.reply_text("РђР»СЊСЏРЅСЃ: " + ", ".join([f"@{u}" for u in mentioned]))


async def vragi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    mentioned = get_mentioned_users(msg.text)

    if len(mentioned) < 2:
        await msg.reply_text("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /vragi @user1 @user2")
        return

    marriages.setdefault(chat_id, [])
    marriages[chat_id].append(
        {"type": "enemies", "members": mentioned, "date": datetime.now().isoformat()}
    )
    save_json(DATA_FILE, marriages)
    await msg.reply_text("Р’СЂР°РіРё: " + ", ".join([f"@{u}" for u in mentioned]))


async def braki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    records = marriages.get(chat_id, [])

    lines = [f"{' вќ¤пёЏ '.join([f'@{u}' for u in m['members']])}" for m in records if m.get("type") == "marriage"]
    if not lines:
        await msg.reply_text("РќРµС‚ Р±СЂР°РєРѕРІ")
        return
    await msg.reply_text("Р‘СЂР°РєРё:\n" + "\n".join(lines))


async def soyuzy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    records = marriages.get(chat_id, [])

    unions = [f"{', '.join([f'@{u}' for u in m['members']])}" for m in records if m.get("type") == "union"]
    enemies = [f"{', '.join([f'@{u}' for u in m['members']])}" for m in records if m.get("type") == "enemies"]

    if not unions and not enemies:
        await msg.reply_text("РќРµС‚ СЃРѕСЋР·РѕРІ Рё РІСЂР°РіРѕРІ")
        return

    text = "РђР»СЊСЏРЅСЃС‹:\n" + ("\n".join(unions) if unions else "РЅРµС‚")
    text += "\n\nР’СЂР°РіРё:\n" + ("\n".join(enemies) if enemies else "РЅРµС‚")
    await msg.reply_text(text)


async def moisoyuz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    user = msg.from_user.username or str(msg.from_user.id)
    records = marriages.get(chat_id, [])

    lines = []
    for m in records:
        members = m.get("members", [])
        if user not in members:
            continue
        if m.get("type") == "marriage":
            lines.append("Р‘СЂР°Рє: " + " вќ¤пёЏ ".join([f"@{u}" for u in members]))
        elif m.get("type") == "union":
            lines.append("РђР»СЊСЏРЅСЃ: " + ", ".join([f"@{u}" for u in members]))
        elif m.get("type") == "enemies":
            lines.append("Р’СЂР°РіРё: " + ", ".join([f"@{u}" for u in members]))

    if not lines:
        await msg.reply_text("РўС‹ РЅРёРіРґРµ РЅРµ СЃРѕСЃС‚РѕРёС€СЊ")
        return
    await msg.reply_text(f"РЎРїРёСЃРѕРє РґР»СЏ @{user}:\n" + "\n".join(lines))


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    challenger = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)

    if len(mentioned) != 1:
        await msg.reply_text("Использование: /duel @user")
        return

    target = mentioned[0]
    if target == challenger:
        await msg.reply_text("Нельзя вызвать себя на дуэль")
        return

    key = duel_key(chat_id, challenger, target)
    if key in duel_requests:
        await msg.reply_text("Вызов уже отправлен")
        return

    active_key = make_active_duel_key(chat_id, challenger, target)
    if active_key in active_duels:
        await msg.reply_text("Между этими игроками уже идет дуэль")
        return

    # Clear stale requests for the same pair so /duel can be resent cleanly.
    clear_duel_requests_for_pair(chat_id, challenger, target)

    duel_requests[key] = {"chat_id": chat_id, "challenger": challenger, "target": target}
    await msg.reply_text(
        f"@{challenger} вызывает @{target} на дуэль!\n"
        "Кликабельные команды:\n"
        "/accept - принять\n"
        "/decline - отклонить"
    )


async def accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    target = msg.from_user.username or str(msg.from_user.id)

    key, req = find_request_for_target(chat_id, target)
    if not req:
        await msg.reply_text("Для тебя нет активных дуэлей")
        return

    challenger = req["challenger"]
    clear_duel_requests_for_pair(chat_id, challenger, target)

    battle_key = make_active_duel_key(chat_id, challenger, target)
    first_turn = random.choice([challenger, target])
    active_duels[battle_key] = {
        "chat_id": chat_id,
        "a": challenger,
        "b": target,
        "turn": first_turn,
        "hp_a": 100,
        "hp_b": 100,
        "shots_done": 0,
    }

    await msg.reply_text(
        f"Дуэль началась: @{challenger} vs @{target}\n"
        f"Первый ход: @{first_turn}\n"
        f"HP @{challenger}: 100/100 {hp_bar(100)}\n"
        f"HP @{target}: 100/100 {hp_bar(100)}\n"
        "Команда выстрела: /shot"
    )


async def shot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    shooter = msg.from_user.username or str(msg.from_user.id)

    battle_key, duel_state = find_active_duel_for_user(chat_id, shooter)
    if not duel_state:
        await msg.reply_text("У тебя нет активной дуэли. Начни: /duel @user")
        return

    turn = duel_state.get("turn")
    if shooter != turn:
        await msg.reply_text(f"Сейчас ход не твой. Ходит: @{turn}")
        return

    a = duel_state.get("a")
    b = duel_state.get("b")
    opponent = b if shooter == a else a
    hp_a = int(duel_state.get("hp_a", 100))
    hp_b = int(duel_state.get("hp_b", 100))
    shots_done = int(duel_state.get("shots_done", 0))

    # У всех равные шансы: попадание 50/50.
    hit = random.random() < 0.5
    shots_done += 1

    if hit:
        damage = random.randint(18, 34)
        if shooter == a:
            hp_b = max(0, hp_b - damage)
        else:
            hp_a = max(0, hp_a - damage)
        shot_text = f"Попадание! @{shooter} нанес {damage} урона."
    else:
        shot_text = f"Промах! @{shooter} не попал."

    duel_state["hp_a"] = hp_a
    duel_state["hp_b"] = hp_b
    duel_state["shots_done"] = shots_done
    duel_state["turn"] = opponent
    active_duels[battle_key] = duel_state

    status_text = (
        f"{shot_text}\n"
        f"Выстрелы: {shots_done}\n"
        f"HP @{a}: {hp_a}/100 {hp_bar(hp_a)}\n"
        f"HP @{b}: {hp_b}/100 {hp_bar(hp_b)}"
    )

    # Завершение только если у одного из игроков закончилось HP.
    finished = hp_a <= 0 or hp_b <= 0
    if not finished:
        await msg.reply_text(status_text + f"\nСледующий ход: @{opponent}\nКоманда: /shot")
        return

    ensure_stat_user(duel_stats, a)
    ensure_stat_user(duel_stats, b)

    if hp_a == hp_b:
        duel_stats[a]["draws"] += 1
        duel_stats[b]["draws"] += 1
        result_text = "Итог дуэли: ничья."
    elif hp_a > hp_b:
        duel_stats[a]["wins"] += 1
        duel_stats[b]["losses"] += 1
        result_text = f"Итог дуэли: победил @{a}."
    else:
        duel_stats[b]["wins"] += 1
        duel_stats[a]["losses"] += 1
        result_text = f"Итог дуэли: победил @{b}."

    active_duels.pop(battle_key, None)
    save_json(DUEL_STATS_FILE, duel_stats)
    await msg.reply_text(status_text + "\n" + result_text + "\nНовая дуэль: /duel @user")


async def decline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    target = msg.from_user.username or str(msg.from_user.id)

    key, req = find_request_for_target(chat_id, target)
    if not req:
        await msg.reply_text("Для тебя нет активных дуэлей")
        return

    challenger = req["challenger"]
    clear_duel_requests_for_pair(chat_id, challenger, target)
    await msg.reply_text(f"@{target} отклонил(а) дуэль от @{challenger}")


async def duel_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Дуэли (кликабельно):\n"
        "/duel @user\n"
        "/accept\n"
        "/decline\n"
        "/shot\n"
        "/pvpstats [@user]\n"
        "/pvptop\n\n"
        "Правила:\n"
        "1) У каждого 100 HP\n"
        "2) Попадание / промах: 50/50\n"
        "3) При промахе ход переходит сопернику\n"
        "4) Дуэль идет до 0 HP у одного из игроков"
    )

async def pvpstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)
    user = mentioned[0] if mentioned else caller

    ensure_stat_user(duel_stats, user)
    s = duel_stats[user]
    await msg.reply_text(
        f"PvP @{user}\n"
        f"РџРѕР±РµРґС‹: {s['wins']}\n"
        f"РџРѕСЂР°Р¶РµРЅРёСЏ: {s['losses']}\n"
        f"РќРёС‡СЊРё: {s['draws']}"
    )


async def pvptop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not duel_stats:
        await msg.reply_text("РџРѕРєР° РЅРµС‚ PvP-РґР°РЅРЅС‹С…")
        return

    ranking = sorted(
        duel_stats.items(),
        key=lambda item: (item[1].get("wins", 0), -item[1].get("losses", 0)),
        reverse=True,
    )[:10]

    lines = ["РўРѕРї PvP (РїРѕ РїРѕР±РµРґР°Рј):"]
    for i, (user, s) in enumerate(ranking, start=1):
        lines.append(f"{i}. @{user} - W:{s.get('wins', 0)} L:{s.get('losses', 0)} D:{s.get('draws', 0)}")
    await msg.reply_text("\n".join(lines))


async def war(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    attacker = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)

    if len(mentioned) != 1:
        await msg.reply_text("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /war @user")
        return

    defender = mentioned[0]
    if defender == attacker:
        await msg.reply_text("РќРµР»СЊР·СЏ РІРѕРµРІР°С‚СЊ СЃ СЃРѕР±РѕР№")
        return

    hp_a = 120
    hp_d = 120
    log_lines = [f"Р’РѕР№РЅР°: @{attacker} vs @{defender}"]
    for round_num in range(1, 6):
        if hp_a <= 0 or hp_d <= 0:
            break
        dmg_a = random.randint(12, 35)
        dmg_d = random.randint(12, 35)
        hp_d -= dmg_a
        hp_a -= dmg_d
        log_lines.append(
            f"Р Р°СѓРЅРґ {round_num}: @{attacker} -{dmg_a} HP РІСЂР°РіР°, @{defender} -{dmg_d} HP РІСЂР°РіР° | "
            f"HP: {max(hp_a,0)}:{max(hp_d,0)}"
        )

    ensure_stat_user(war_stats, attacker)
    ensure_stat_user(war_stats, defender)

    if hp_a == hp_d:
        war_stats[attacker]["draws"] += 1
        war_stats[defender]["draws"] += 1
        log_lines.append("РС‚РѕРі: РЅРёС‡СЊСЏ")
    elif hp_a > hp_d:
        war_stats[attacker]["wins"] += 1
        war_stats[defender]["losses"] += 1
        log_lines.append(f"РС‚РѕРі: РїРѕР±РµРґРёР» @{attacker}")
    else:
        war_stats[defender]["wins"] += 1
        war_stats[attacker]["losses"] += 1
        log_lines.append(f"РС‚РѕРі: РїРѕР±РµРґРёР» @{defender}")

    save_json(WAR_STATS_FILE, war_stats)
    await msg.reply_text("\n".join(log_lines))


async def warstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)
    user = mentioned[0] if mentioned else caller

    ensure_stat_user(war_stats, user)
    s = war_stats[user]
    await msg.reply_text(
        f"Р’РѕР№РЅС‹ @{user}\n"
        f"РџРѕР±РµРґС‹: {s['wins']}\n"
        f"РџРѕСЂР°Р¶РµРЅРёСЏ: {s['losses']}\n"
        f"РќРёС‡СЊРё: {s['draws']}"
    )


async def wartop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not war_stats:
        await msg.reply_text("РџРѕРєР° РЅРµС‚ РґР°РЅРЅС‹С… РїРѕ РІРѕР№РЅР°Рј")
        return

    ranking = sorted(
        war_stats.items(),
        key=lambda item: (item[1].get("wins", 0), -item[1].get("losses", 0)),
        reverse=True,
    )[:10]

    lines = ["РўРѕРї РІРѕР№РЅ (РїРѕ РїРѕР±РµРґР°Рј):"]
    for i, (user, s) in enumerate(ranking, start=1):
        lines.append(f"{i}. @{user} - W:{s.get('wins', 0)} L:{s.get('losses', 0)} D:{s.get('draws', 0)}")
    await msg.reply_text("\n".join(lines))


async def words_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)

    word_games[chat_id] = {
        "active": True,
        "last_letter": "",
        "used_words": [],
        "last_user": "",
    }
    save_json(WORD_GAME_FILE, word_games)
    await msg.reply_text("Игра в слова запущена. Пишите: /word слово")


async def word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    user = msg.from_user.username or str(msg.from_user.id)
    game = word_games.get(chat_id)

    if not game or not game.get("active"):
        await msg.reply_text("Сначала запусти игру: /words_start")
        return

    if not context.args:
        await msg.reply_text("Использование: /word слово")
        return

    raw_word = " ".join(context.args).strip()
    w = normalize_word(raw_word)
    if len(w) < 2:
        await msg.reply_text("Слово слишком короткое")
        return

    used_words = set(game.get("used_words", []))
    if w in used_words:
        await msg.reply_text("Это слово уже было")
        return

    required = game.get("last_letter", "")
    if required and not w.startswith(required):
        await msg.reply_text(f"Нужна буква: {required.upper()}")
        return

    last_user = game.get("last_user", "")
    if last_user == user:
        await msg.reply_text("Сейчас ход другого игрока")
        return

    used_words.add(w)
    next_letter = get_last_letter(w)

    game["used_words"] = sorted(list(used_words))
    game["last_letter"] = next_letter
    game["last_user"] = user
    word_games[chat_id] = game
    save_json(WORD_GAME_FILE, word_games)

    if next_letter:
        await msg.reply_text(f"Принято: {w}. Следующая буква: {next_letter.upper()}")
    else:
        await msg.reply_text(f"Принято: {w}. Следующая буква: ЛЮБАЯ")


async def words_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    game = word_games.get(chat_id)
    if not game or not game.get("active"):
        await msg.reply_text("Игра не запущена")
        return

    await msg.reply_text(
        "Игра активна\n"
        f"Слов использовано: {len(game.get('used_words', []))}\n"
        f"Текущая буква: {(game.get('last_letter') or 'любая').upper()}"
    )


async def words_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    game = word_games.get(chat_id)
    if not game or not game.get("active"):
        await msg.reply_text("Игра уже остановлена")
        return

    count_words = len(game.get("used_words", []))
    word_games[chat_id] = {
        "active": False,
        "last_letter": "",
        "used_words": [],
        "last_user": "",
    }
    save_json(WORD_GAME_FILE, word_games)
    await msg.reply_text(f"Игра остановлена. Всего слов: {count_words}")


async def raid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    state = raid_states.get(chat_id, {})
    if state.get("active"):
        await msg.reply_text("Р РµР№Рґ СѓР¶Рµ РёРґРµС‚. РСЃРїРѕР»СЊР·СѓР№С‚Рµ /raid_hit")
        return

    boss_names = ["Р”СЂР°РєРѕРЅ", "РўРёС‚Р°РЅ", "Р›РёС‡", "Р“РёРґСЂР°", "Р“РѕР»РµРј"]
    hp = random.randint(350, 600)
    boss = random.choice(boss_names)
    raid_states[chat_id] = {"active": True, "boss": boss, "hp": hp, "max_hp": hp, "attackers": {}}
    save_json(RAID_FILE, raid_states)
    await msg.reply_text(f"Р РµР№Рґ РЅР°С‡Р°Р»СЃСЏ!\nР‘РѕСЃСЃ: {boss}\nHP: {hp}\nР‘РµР№С‚Рµ Р±РѕСЃСЃР°: /raid_hit")


async def raid_hit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    state = raid_states.get(chat_id)
    if not state or not state.get("active"):
        await msg.reply_text("РЎРµР№С‡Р°СЃ РЅРµС‚ Р°РєС‚РёРІРЅРѕРіРѕ СЂРµР№РґР°. РљРѕРјР°РЅРґР°: /raid_start")
        return

    user = get_user_key(msg.from_user.username, msg.from_user.id)
    damage = random.randint(25, 85)
    if random.random() < 0.15:
        damage = int(damage * 1.7)

    state["hp"] = max(0, int(state["hp"]) - damage)
    attackers = state.setdefault("attackers", {})
    attackers[user] = int(attackers.get(user, 0)) + damage

    if state["hp"] > 0:
        await msg.reply_text(
            f"@{user} РЅР°РЅРµСЃ {damage} СѓСЂРѕРЅР°.\n"
            f"Р‘РѕСЃСЃ {state['boss']} HP: {state['hp']}/{state['max_hp']}"
        )
        save_json(RAID_FILE, raid_states)
        return

    state["active"] = False
    top = sorted(attackers.items(), key=lambda x: x[1], reverse=True)[:5]
    lines = [f"Р‘РѕСЃСЃ {state['boss']} РїРѕРІРµСЂР¶РµРЅ!"]
    if top:
        lines.append("РўРѕРї СѓСЂРѕРЅР°:")
        for i, (name, dmg) in enumerate(top, start=1):
            lines.append(f"{i}. @{name}: {dmg}")

    for name, dmg in attackers.items():
        reward = 10 + dmg // 20
        entry = daily_rewards.setdefault(name, {"coins": 0, "streak": 0, "last_claim": ""})
        entry["coins"] = int(entry.get("coins", 0)) + reward

    save_json(RAID_FILE, raid_states)
    save_json(DAILY_FILE, daily_rewards)
    await msg.reply_text("\n".join(lines) + "\nРќР°РіСЂР°РґС‹ РґРѕР±Р°РІР»РµРЅС‹ РІ Р±Р°Р»Р°РЅСЃ.")


async def raid_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    state = raid_states.get(chat_id)
    if not state or not state.get("active"):
        await msg.reply_text("РђРєС‚РёРІРЅРѕРіРѕ СЂРµР№РґР° РЅРµС‚.")
        return

    attackers = state.get("attackers", {})
    top = sorted(attackers.items(), key=lambda x: x[1], reverse=True)[:3]
    text = f"Р‘РѕСЃСЃ: {state['boss']}\nHP: {state['hp']}/{state['max_hp']}"
    if top:
        text += "\nРўРѕРї СѓСЂРѕРЅР°:\n" + "\n".join([f"{i}. @{u}: {d}" for i, (u, d) in enumerate(top, 1)])
    await msg.reply_text(text)


async def raid_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    ranking = sorted(
        ((u, int(v.get("coins", 0))) for u, v in daily_rewards.items()),
        key=lambda x: x[1],
        reverse=True,
    )[:10]
    if not ranking:
        await msg.reply_text("РџРѕРєР° РЅРµС‚ РґР°РЅРЅС‹С….")
        return

    lines = ["РўРѕРї РїРѕ РјРѕРЅРµС‚Р°Рј:"]
    for i, (u, c) in enumerate(ranking, start=1):
        lines.append(f"{i}. @{u}: {c} РјРѕРЅРµС‚")
    await msg.reply_text("\n".join(lines))


async def raid_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Рейд (кликабельно):\n"
        "/raid_start\n"
        "/raid_hit\n"
        "/raid_status\n"
        "/raid_top"
    )


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = get_user_key(msg.from_user.username, msg.from_user.id)
    entry = daily_rewards.setdefault(user, {"coins": 0, "streak": 0, "last_claim": ""})
    today = today_str()
    last = entry.get("last_claim", "")

    if last == today:
        await msg.reply_text(f"РЎРµРіРѕРґРЅСЏ СѓР¶Рµ РїРѕР»СѓС‡РµРЅРѕ.\nР‘Р°Р»Р°РЅСЃ: {entry.get('coins', 0)} РјРѕРЅРµС‚")
        return

    days_diff = 99
    if last:
        try:
            days_diff = (datetime.fromisoformat(today).date() - datetime.fromisoformat(last).date()).days
        except Exception:
            days_diff = 99

    if days_diff == 1:
        entry["streak"] = int(entry.get("streak", 0)) + 1
    else:
        entry["streak"] = 1

    reward = 50 + min(50, entry["streak"] * 5)
    entry["coins"] = int(entry.get("coins", 0)) + reward
    entry["last_claim"] = today
    daily_rewards[user] = entry
    save_json(DAILY_FILE, daily_rewards)

    await msg.reply_text(
        f"Р•Р¶РµРґРЅРµРІРЅР°СЏ РЅР°РіСЂР°РґР°: +{reward}\n"
        f"РЎРµСЂРёСЏ: {entry['streak']} РґРЅРµР№\n"
        f"Р‘Р°Р»Р°РЅСЃ: {entry['coins']} РјРѕРЅРµС‚"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = get_user_key(msg.from_user.username, msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)
    user = mentioned[0] if mentioned else caller
    entry = daily_rewards.setdefault(user, {"coins": 0, "streak": 0, "last_claim": ""})
    await msg.reply_text(
        f"Р‘Р°Р»Р°РЅСЃ @{user}: {entry.get('coins', 0)} РјРѕРЅРµС‚\n"
        f"РЎРµСЂРёСЏ daily: {entry.get('streak', 0)}"
    )


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["Магазин:"]
    for item_id, item in SHOP_ITEMS.items():
        lines.append(f"- {item_id}: {item['name']} ({item['price']} монет)")
    lines.append("Покупка: /buy item_id [кол-во]")
    await update.message.reply_text("\n".join(lines))


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = get_user_key(msg.from_user.username, msg.from_user.id)

    if not context.args:
        await msg.reply_text("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /buy item_id [РєРѕР»-РІРѕ]")
        return

    item_id = context.args[0].lower().strip()
    item = SHOP_ITEMS.get(item_id)
    if not item:
        await msg.reply_text("РўР°РєРѕРіРѕ РїСЂРµРґРјРµС‚Р° РЅРµС‚. РЎРїРёСЃРѕРє: /shop")
        return

    qty = 1
    if len(context.args) > 1:
        try:
            qty = int(context.args[1])
        except ValueError:
            await msg.reply_text("РљРѕР»РёС‡РµСЃС‚РІРѕ РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ С‡РёСЃР»РѕРј")
            return

    if qty < 1 or qty > 99:
        await msg.reply_text("РљРѕР»РёС‡РµСЃС‚РІРѕ: РѕС‚ 1 РґРѕ 99")
        return

    cost = item["price"] * qty
    wallet = ensure_wallet(user)
    coins = int(wallet.get("coins", 0))
    if coins < cost:
        await msg.reply_text(f"РќРµ С…РІР°С‚Р°РµС‚ РјРѕРЅРµС‚. РќСѓР¶РЅРѕ: {cost}, Сѓ С‚РµР±СЏ: {coins}")
        return

    wallet["coins"] = coins - cost
    inv = ensure_inventory(user)
    inv[item_id] = int(inv.get(item_id, 0)) + qty

    save_json(DAILY_FILE, daily_rewards)
    save_json(INVENTORY_FILE, inventories)
    await msg.reply_text(
        f"РџРѕРєСѓРїРєР° СѓСЃРїРµС€РЅР°: {item['name']} x{qty}\n"
        f"РЎРїРёСЃР°РЅРѕ: {cost}\n"
        f"Р‘Р°Р»Р°РЅСЃ: {wallet['coins']}"
    )


async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = get_user_key(msg.from_user.username, msg.from_user.id)
    mentioned = get_mentioned_users(msg.text)
    user = mentioned[0] if mentioned else caller

    inv = ensure_inventory(user)
    if not inv:
        await msg.reply_text(f"РРЅРІРµРЅС‚Р°СЂСЊ @{user} РїСѓСЃС‚")
        return

    lines = [f"РРЅРІРµРЅС‚Р°СЂСЊ @{user}:"]
    for item_id, qty in sorted(inv.items()):
        item = SHOP_ITEMS.get(item_id, {"name": item_id})
        lines.append(f"- {item['name']} ({item_id}): x{qty}")
    await msg.reply_text("\n".join(lines))


async def eco_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Экономика (кликабельно):\n"
        "/daily\n"
        "/balance [@user]\n"
        "/shop\n"
        "/buy item_id [кол-во]\n"
        "/inventory [@user]"
        "\n\n"
        "Мопс-Фармила:\n"
        "/mops_on\n"
        "/mops_off\n"
        "/mops_status"
    )


async def mops_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    cfg = ensure_mops_chat(chat_id)
    cfg["enabled"] = True
    save_json(MOPS_FILE, mops_state)
    await update.message.reply_text(
        "🐶 Мопс-Фармила включен.\n"
        "Буду писать ежедневный отчёт о стабильности бота."
    )


async def mops_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    cfg = ensure_mops_chat(chat_id)
    cfg["enabled"] = False
    save_json(MOPS_FILE, mops_state)
    await update.message.reply_text("🐶 Мопс-Фармила выключен для этого чата.")


async def mops_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    cfg = ensure_mops_chat(chat_id)
    uptime = format_uptime(datetime.now() - BOT_STARTED_AT)
    status = "включен" if cfg.get("enabled") else "выключен"
    last_sent = cfg.get("last_sent") or "еще не отправлялся"
    await update.message.reply_text(
        f"🐶 Мопс-Фармила: {status}\n"
        f"Аптайм: {uptime}\n"
        f"Последний daily-отчёт: {last_sent}"
    )


async def mops_daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chats = mops_state.setdefault("chats", {})
    today = today_str()
    uptime = format_uptime(datetime.now() - BOT_STARTED_AT)

    for chat_id, cfg in chats.items():
        if not cfg.get("enabled", True):
            continue
        if cfg.get("last_sent") == today:
            continue
        try:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    "🐶 Мопс-Фармила на связи.\n"
                    "✅ Бот работает стабильно.\n"
                    f"⏱ Аптайм: {uptime}\n"
                    "Если что-то пойдёт не так, просто напишите команду /mops_status."
                ),
            )
            cfg["last_sent"] = today
        except Exception as e:
            logger.warning("Mops daily message failed for chat %s: %s", chat_id, e)

    save_json(MOPS_FILE, mops_state)


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brak", brak))
    app.add_handler(CommandHandler("razvod", razvod))
    app.add_handler(CommandHandler("alyans", alyans))
    app.add_handler(CommandHandler("vragi", vragi))
    app.add_handler(CommandHandler("braki", braki))
    app.add_handler(CommandHandler("soyuzy", soyuzy))
    app.add_handler(CommandHandler("moisoyuz", moisoyuz))
    app.add_handler(CommandHandler("anniversary", anniversary))
    app.add_handler(CommandHandler("rings", rings))
    app.add_handler(CommandHandler("ring_exchange", ring_exchange))
    app.add_handler(CommandHandler("duel", duel))
    app.add_handler(CommandHandler("pvp", duel))
    app.add_handler(CommandHandler("accept", accept))
    app.add_handler(CommandHandler("decline", decline))
    app.add_handler(CommandHandler("shot", shot))
    app.add_handler(CommandHandler("duel_help", duel_help))
    app.add_handler(CommandHandler("pvpstats", pvpstats))
    app.add_handler(CommandHandler("pvptop", pvptop))
    app.add_handler(CommandHandler("war", war))
    app.add_handler(CommandHandler("voyna", war))
    app.add_handler(CommandHandler("warstats", warstats_cmd))
    app.add_handler(CommandHandler("wartop", wartop))
    app.add_handler(CommandHandler("words_start", words_start))
    app.add_handler(CommandHandler("word", word))
    app.add_handler(CommandHandler("words_status", words_status))
    app.add_handler(CommandHandler("words_stop", words_stop))
    app.add_handler(CommandHandler("raid_start", raid_start))
    app.add_handler(CommandHandler("raid_hit", raid_hit))
    app.add_handler(CommandHandler("raid_status", raid_status))
    app.add_handler(CommandHandler("raid_top", raid_top))
    app.add_handler(CommandHandler("raid_help", raid_help))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("eco_help", eco_help))
    app.add_handler(CommandHandler("mops_on", mops_on))
    app.add_handler(CommandHandler("mops_off", mops_off))
    app.add_handler(CommandHandler("mops_status", mops_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, marriage_ceremony_text))
    return app


def main() -> None:
    global marriages, duel_stats, war_stats, word_games, raid_states, daily_rewards, inventories, mops_state
    marriages = load_json(DATA_FILE, {})
    duel_stats = load_json(DUEL_STATS_FILE, {})
    war_stats = load_json(WAR_STATS_FILE, {})
    word_games = load_json(WORD_GAME_FILE, {})
    raid_states = load_json(RAID_FILE, {})
    daily_rewards = load_json(DAILY_FILE, {})
    inventories = load_json(INVENTORY_FILE, {})
    mops_state = load_json(MOPS_FILE, {"chats": {}})

    token = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Set BOT_TOKEN (or TELEGRAM_BOT_TOKEN) in environment")

    app = build_application(token)
    if app.job_queue:
        # First check after 2 minutes, then every 24 hours.
        app.job_queue.run_repeating(mops_daily_job, interval=86400, first=120, name="mops_daily")
    logger.info("Bot is running in polling mode")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


