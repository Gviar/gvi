import json
import logging
import os
import random
import psutil
import re
from datetime import datetime, timedelta

from telegram import Message, Update
from telegram.ext import Application, ChatMemberHandler, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# === РУССКИЕ КОМАНДЫ БЕЗ / ===
def extract_users_from_tokens(tokens: list[str] | None) -> list[str]:
    if not tokens:
        return []

    users: list[str] = []
    for token in tokens:
        cleaned = (token or "").strip().strip(",.;:!?()[]{}<>")
        if not cleaned:
            continue
        if cleaned.startswith("@"):
            cleaned = cleaned[1:]
        if re.fullmatch(r"[A-Za-z0-9_]{3,32}", cleaned):
            users.append(cleaned)

    return list(dict.fromkeys(users))


def get_mentioned_or_replied(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> list[str]:
    msg = update.message
    if not msg:
        return []

    mentioned = get_mentioned_users(msg.text)
    mentioned.extend(extract_users_from_tokens(getattr(context, "args", None)))
    if mentioned:
        return list(dict.fromkeys(mentioned))

    if msg.reply_to_message and msg.reply_to_message.from_user:
        user = msg.reply_to_message.from_user
        return [user.username or str(user.id)]

    return []

async def handle_ru_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text.strip().lower()
    user = msg.from_user
    if not user:
        return
    user_key = register_profile(user)
    chat_id = str(msg.chat_id)
    ensure_mops_chat(chat_id)
    remember_chat_user(chat_id, user_key)
    if await moderation_guard(update, context, chat_id, user_key):
        return

    ceremony_words = {
        "согласен",
        "согласна",
        "не согласен",
        "не согласна",
        "против",
        "отказываюсь",
    }
    if text in ceremony_words:
        pending_user = norm_user(msg.from_user.username or str(msg.from_user.id))
        _, pending = find_pending_marriage_for_user(chat_id, pending_user)
        if pending:
            return

    # === СКРЫТЫЕ КОМАНДЫ СОЗДАТЕЛЯ ===
    if text.startswith("фармила-прем "):
        await owner_grant_premium(update, context)
        return
    if text.startswith("фармила-монеты "):
        await owner_grant_coins(update, context)
        return
    if text.startswith("фармила-жалоба "):
        await owner_secret_report(update, context)
        return
    if text.startswith("фармила-мод "):
        await owner_mod_config(update, context)
        return

    # === КОМАНДЫ МОПСА ===
    if text in ['привет', 'приветики', 'хау', 'здорово', 'hi', 'hello']:
        await mops_greet(update, context)
        return
    if text in ['пока', 'бай', 'до свидания', 'goodbye']:
        await mops_farewell(update, context)
        return
    if text in ['спасибо', 'благодарю', 'thx', 'thanks']:
        await mops_thanks(update, context)
        return
    if text in ['шутка', 'анекдот', 'рассмеши']:
        await mops_joke(update, context)
        return
    if text in ['цитата', 'quote']:
        await mops_quote(update, context)
        return
    if text in ['факт', 'интересное', 'инфа']:
        await mops_fact(update, context)
        return
    if text in ['комплимент', 'похвали']:
        await mops_compliment(update, context)
        return
    if text in ['оскорбление', 'поругай']:
        await mops_insult(update, context)
        return
    if text in ['шар', 'ball', 'спрошу']:
        await mops_8ball(update, context)
        return
    if text in ['монетка', 'орел', 'решка']:
        await mops_coin(update, context)
        return
    if text in ['кубик', 'бросить']:
        await mops_dice(update, context)
        return
    if text in ['число', 'рандом', 'рандомное']:
        await mops_random(update, context)
        return
    if text in ['гороскоп', 'зодиак']:
        await mops_horoscope(update, context)
        return
    if text in ['погода', 'погодка']:
        await mops_weather(update, context)
        return
    if text in ['помощь', 'help', 'команды', 'что ты умеешь']:
        await mops_farmila_help(update, context)
        return
    if text in ['хоши', 'кошка', 'hoshi']:
        await hoshi_help(update, context)
        return
    if text in ['совет хоши', 'хоши совет', 'hoshi tip']:
        await hoshi_tip(update, context)
        return

    # === ИГРОВЫЕ КОМАНДЫ ===
    game_commands = {
        'дуэль': duel, 'брак': brak, 'свадьба': brak,
        'развод': razvod, 'расставание': razvod,
        'альянс': alyans, 'союз': alyans,
        'враги': vragi, 'война': war,
        'принять': accept, 'согласен': accept,
        'отклонить': decline, 'отказ': decline,
        'выстрел': shot, 'стрель': shot,
        'баланс': balance, 'монеты': balance,
        'магазин': shop, 'кольца': rings,
        'моикольца': my_rings, 'кольцо': ring_exchange,
        'браки': braki, 'семьи': braki, 'союзы': soyuzy,
        'мойбрак': moisoyuz, 'моясемья': moisoyuz,
        'пвп': pvpstats, 'пвптоп': pvptop,
        'войнытоп': wartop, 'рейд': raid_start,
        'удар': raid_hit, 'слова': words_start,
        'стопслова': words_stop,
        'мопс': mops_status, 'мопсон': mops_on,
        'мопсофф': mops_off,
        'ежедневка': daily, 'награда': daily,
        'отношения': relation_status,
        'игры': mops_play,
        'мопсигра': mops_guess,
        'полечудес': polesudes_start,
        'морскойбой': battleship_start,
        'принятьобмен': accept_trade,
        'отклонитьобмен': decline_trade,
        'профиль': profile,
        'квест': quest_status,
        'рыбалка': fish,
        'лотобилет': lottery_buy,
        'лотерея': lottery_draw,
        'тренировкамопса': mops_train,
        'топигроков': top_players,
        'банк': bank,
        'ранг': rank,
    }

    if text in game_commands:
        func = game_commands[text]
        no_args = [
            'принять', 'согласен', 'отклонить', 'отказ', 'выстрел', 'стрель',
            'баланс', 'монеты', 'магазин', 'кольца', 'моикольца', 'браки', 'семьи', 'союзы',
            'мойбрак', 'моясемья', 'пвптоп', 'войнытоп', 'рейд',
            'удар', 'слова', 'стопслова', 'мопс', 'ежедневка', 'награда',
            'отношения', 'игры', 'мопсигра', 'полечудес', 'морскойбой',
            'принятьобмен', 'отклонитьобмен', 'профиль', 'квест', 'рыбалка',
            'лотобилет', 'лотерея', 'тренировкамопса', 'топигроков', 'банк'
            , 'ранг'
        ]

        if text in no_args:
            await func(update, context)
            return

        mentioned = get_mentioned_or_replied(update, context)
        if not mentioned:
            await msg.reply_text(f"Напиши: {text} @user")
            return

        context.args = mentioned
        await func(update, context)
        return

    # Команды с аргументами
    if text.startswith("гвиар кто"):
        await gviar_who(update, context)
        return

    for cmd in ['дуэль ', 'брак ', 'свадьба ', 'развод ', 'расставание ', 'альянс ', 'союз ',
                'враги ', 'война ', 'кольцо ', 'купить ', 'пвп ', 'кто ', 'поцелуй ', 'обнять ',
                'обмен ', 'вклад ', 'снять ', 'передать ']:
        if text.startswith(cmd):
            rest = msg.text.strip()[len(cmd):].strip()
            if rest:
                mentioned = list(dict.fromkeys(re.findall(r'@(\w+)', rest)))
            else:
                mentioned = get_mentioned_or_replied(update, context)

            if not mentioned and cmd.strip() not in ['купить']:
                await msg.reply_text(f"{cmd.strip()} @user")
                return

            cmd_stripped = cmd.strip()
            if cmd_stripped == 'дуэль':
                context.args = mentioned
                await duel(update, context)
            elif cmd_stripped in ['брак', 'свадьба']:
                context.args = mentioned
                await brak(update, context)
            elif cmd_stripped in ['развод', 'расставание']:
                context.args = mentioned
                await razvod(update, context)
            elif cmd_stripped in ['альянс', 'союз']:
                context.args = mentioned
                await alyans(update, context)
            elif cmd_stripped == 'враги':
                context.args = mentioned
                await vragi(update, context)
            elif cmd_stripped == 'война':
                context.args = mentioned
                await war(update, context)
            elif cmd_stripped == 'кольцо':
                context.args = mentioned
                await ring_exchange(update, context)
            elif cmd_stripped == 'купить':
                context.args = [rest]
                await buy(update, context)
            elif cmd_stripped == 'пвп':
                context.args = mentioned
                await pvpstats(update, context)
            elif cmd_stripped == 'кто':
                await msg.reply_text("Используй формат: гвиар кто <вопрос>")
            elif cmd_stripped == 'поцелуй':
                context.args = mentioned
                await mops_kiss(update, context)
            elif cmd_stripped == 'обнять':
                context.args = mentioned
                await mops_hug(update, context)
            elif cmd_stripped == 'обмен':
                context.args = rest.split()
                await trade(update, context)
            elif cmd_stripped == 'вклад':
                context.args = rest.split()
                await deposit(update, context)
            elif cmd_stripped == 'снять':
                context.args = rest.split()
                await withdraw(update, context)
            elif cmd_stripped == 'передать':
                context.args = rest.split()
                await pay(update, context)
            return



def _fix_mojibake(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    try:
        # Fix strings like "ПСЂёвет" -> "Привет"
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")
DUEL_STATS_FILE = os.path.join(BASE_DIR, "duel_stats.json")
WAR_STATS_FILE = os.path.join(BASE_DIR, "war_stats.json")
WORD_GAME_FILE = os.path.join(BASE_DIR, "word_game.json")
RAID_FILE = os.path.join(BASE_DIR, "raid_state.json")
DAILY_FILE = os.path.join(BASE_DIR, "daily_rewards.json")
INVENTORY_FILE = os.path.join(BASE_DIR, "inventory.json")
MOPS_FILE = os.path.join(BASE_DIR, "mops_helper.json")
PROFILE_FILE = os.path.join(BASE_DIR, "profiles.json")
RELATION_FILE = os.path.join(BASE_DIR, "relations.json")
TRADE_FILE = os.path.join(BASE_DIR, "trade_state.json")
MINIGAME_FILE = os.path.join(BASE_DIR, "minigames.json")
REPORT_FILE = os.path.join(BASE_DIR, "mod_reports.json")
QUEST_FILE = os.path.join(BASE_DIR, "quests.json")
ACHIEV_FILE = os.path.join(BASE_DIR, "achievements.json")
LOTTERY_FILE = os.path.join(BASE_DIR, "lottery.json")
BANK_FILE = os.path.join(BASE_DIR, "bank.json")
XP_FILE = os.path.join(BASE_DIR, "xp.json")
MOD_STATE_FILE = os.path.join(BASE_DIR, "moderation_state.json")

SHOP_ITEMS = {
    "sword": {"name": "Железный меч", "price": 120},
    "shield": {"name": "Щит стража", "price": 110},
    "potion": {"name": "Зелье лечения", "price": 60},
    "bomb": {"name": "Бомба", "price": 95},
    "amulet": {"name": "Амулет удачи", "price": 180},
    "ring": {"name": "Обручальное кольцо", "price": 150},
    "katana": {"name": "Катана ветра", "price": 260},
    "axe": {"name": "Секира грома", "price": 240},
    "spear": {"name": "Копье охотника", "price": 210},
    "crossbow": {"name": "Арбалет дозорного", "price": 230},
    "wand": {"name": "Жезл искр", "price": 280},
    "armor": {"name": "Латный доспех", "price": 300},
}

RING_CHOICES = {
    "ring_12k": {"name": "Кольцо 12 карат", "price": 120},
    "ring_18k": {"name": "Кольцо 18 карат", "price": 220},
    "ring_24k": {"name": "Кольцо 24 карат", "price": 350},
    "ring_silver": {"name": "Серебряное кольцо", "price": 80},
    "ring_rose": {"name": "Кольцо Розовый кварц", "price": 480},
    "ring_royal": {"name": "Королевское кольцо", "price": 700},
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
profiles: dict[str, dict] = {}
relations: dict[str, dict] = {}
trade_requests: dict[str, dict] = {}
minigames: dict[str, dict] = {}
mod_reports: dict[str, list[dict]] = {}
quests: dict[str, dict] = {}
achievements: dict[str, dict] = {}
lottery: dict[str, dict] = {}
bank_data: dict[str, dict] = {}
xp_data: dict[str, dict] = {}
mod_state: dict[str, dict] = {}
BOT_STARTED_AT = datetime.now()
OWNER_USERNAME = "exsep"
OWNER_ID = 7238803158


def is_owner(tg_user) -> bool:
    if not tg_user:
        return False
    uname = (tg_user.username or "").lower().strip("@")
    return tg_user.id == OWNER_ID or uname == OWNER_USERNAME


def is_privileged(tg_user) -> bool:
    return is_owner(tg_user)


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
    return re.sub(r"[^a-zA-Zа-яА-ЯС‘РЃ]", "", word).lower().replace("С‘", "е")


def get_last_letter(word: str) -> str:
    skip = {"ь", "СЉ", "ы"}
    for ch in reversed(word):
        if ch not in skip:
            return ch
    return ""


def get_user_key(username: str | None, user_id: int) -> str:
    return f"id:{user_id}"


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
    chat_cfg = chats.setdefault(chat_id, {"enabled": True, "hoshi_enabled": True, "last_sent": "", "last_scene": ""})
    return chat_cfg


def remember_chat_user(chat_id: str, user_key: str) -> None:
    users = mops_state.setdefault("chat_users", {})
    arr = users.setdefault(chat_id, [])
    if user_key not in arr:
        arr.append(user_key)
        users[chat_id] = arr[-500:]
        mops_state["chat_users"] = users
        save_json(MOPS_FILE, mops_state)


def ensure_mod_chat(chat_id: str) -> dict:
    chats = mod_state.setdefault("chats", {})
    cfg = chats.setdefault(
        chat_id,
        {
            "enabled": True,
            "flood_limit": 6,
            "flood_window_sec": 12,
            "mute_minutes": 10,
            "bad_words": ["скам", "фишинг", "докс", "наркот", "экстрем"],
            "warns": {},
            "banned": [],
            "activity": {},
        },
    )
    return cfg


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
        "/my_rings\n"
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
    proposer = register_profile(msg.from_user)
    mentioned = get_mentioned_or_replied(update, context)

    if len(mentioned) != 1:
        await msg.reply_text("Использование: /brak @user")
        return

    target = resolve_user_key_from_token(mentioned[0]) if mentioned else None
    if not target and msg.reply_to_message and msg.reply_to_message.from_user:
        target = register_profile(msg.reply_to_message.from_user)
    if not target:
        await msg.reply_text("Не удалось определить второго участника.")
        return
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
        f"💌 {display_user(proposer)} зовет {display_user(target)} на свадьбу!\n\n"
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
    user = register_profile(msg.from_user)

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
            f"{display_user(user)} не согласен(на) на брак."
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
    mentioned = get_mentioned_or_replied(update, context)

    if chat_id not in marriages:
        await msg.reply_text("Рќет бСЂаков")
        return

    if mentioned:
        target = mentioned[0]
        for m in list(marriages[chat_id]):
            if m.get("type") == "marriage" and target in m.get("members", []):
                marriages[chat_id].remove(m)
                save_json(DATA_FILE, marriages)
                await msg.reply_text(f"@{target} СЂазведен(а)")
                return
        await msg.reply_text("Рќе найдено")
        return

    found = False
    for m in list(marriages[chat_id]):
        if m.get("type") == "marriage" and user in m.get("members", []):
            marriages[chat_id].remove(m)
            found = True

    if found:
        save_json(DATA_FILE, marriages)
        await msg.reply_text("Р азвод выполнен")
    else:
        await msg.reply_text("Рўы не в бСЂаке")


async def anniversary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    caller = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_or_replied(update, context)
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


async def my_rings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = get_user_key(msg.from_user.username, msg.from_user.id)
    mentioned = get_mentioned_or_replied(update, context)
    user = mentioned[0] if mentioned else caller

    inv = ensure_inventory(user)
    ring_ids = ["ring_24k", "ring_18k", "ring_12k", "ring"]
    lines: list[str] = []
    total = 0

    for ring_id in ring_ids:
        qty = int(inv.get(ring_id, 0))
        if qty <= 0:
            continue
        total += qty
        ring_name = SHOP_ITEMS.get(ring_id, {"name": ring_id}).get("name", ring_id)
        lines.append(f"- {ring_name} ({ring_id}): x{qty}")

    if not lines:
        await msg.reply_text(f"У @{user} нет колец")
        return

    await msg.reply_text(f"Кольца @{user} (всего: {total}):\n" + "\n".join(lines))

async def alyans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    mentioned = get_mentioned_or_replied(update, context)
    actor = msg.from_user.username or str(msg.from_user.id)
    if len(mentioned) == 1 and mentioned[0] != actor:
        mentioned = [actor, mentioned[0]]

    if len(mentioned) < 1 or len(mentioned) > 80:
        await msg.reply_text("Альянс: 1-80 человек")
        return

    marriages.setdefault(chat_id, [])
    marriages[chat_id].append(
        {"type": "union", "members": mentioned, "date": datetime.now().isoformat()}
    )
    save_json(DATA_FILE, marriages)
    await msg.reply_text("Альянс: " + ", ".join([f"@{u}" for u in mentioned]))


async def vragi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    mentioned = get_mentioned_or_replied(update, context)
    actor = msg.from_user.username or str(msg.from_user.id)
    if len(mentioned) == 1 and mentioned[0] != actor:
        mentioned = [actor, mentioned[0]]

    if len(mentioned) < 2:
        await msg.reply_text("Рспользованёе: /vragi @user1 @user2")
        return

    marriages.setdefault(chat_id, [])
    marriages[chat_id].append(
        {"type": "enemies", "members": mentioned, "date": datetime.now().isoformat()}
    )
    save_json(DATA_FILE, marriages)
    await msg.reply_text("ВСЂагё: " + ", ".join([f"@{u}" for u in mentioned]))


async def braki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    records = marriages.get(chat_id, [])

    lines = [f"{' вќ¤пёЏ '.join([f'@{u}' for u in m['members']])}" for m in records if m.get("type") == "marriage"]
    if not lines:
        await msg.reply_text("Рќет бСЂаков")
        return
    await msg.reply_text("БСЂакё:\n" + "\n".join(lines))


async def soyuzy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    records = marriages.get(chat_id, [])

    unions = [f"{', '.join([f'@{u}' for u in m['members']])}" for m in records if m.get("type") == "union"]
    enemies = [f"{', '.join([f'@{u}' for u in m['members']])}" for m in records if m.get("type") == "enemies"]

    if not unions and not enemies:
        await msg.reply_text("Рќет союзов ё вСЂагов")
        return

    text = "Альянсы:\n" + ("\n".join(unions) if unions else "нет")
    text += "\n\nВСЂагё:\n" + ("\n".join(enemies) if enemies else "нет")
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
            lines.append("БСЂак: " + " вќ¤пёЏ ".join([f"@{u}" for u in members]))
        elif m.get("type") == "union":
            lines.append("Альянс: " + ", ".join([f"@{u}" for u in members]))
        elif m.get("type") == "enemies":
            lines.append("ВСЂагё: " + ", ".join([f"@{u}" for u in members]))

    if not lines:
        await msg.reply_text("Рўы нёгде не состоёшь")
        return
    await msg.reply_text(f"Спёсок для @{user}:\n" + "\n".join(lines))


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    challenger = register_profile(msg.from_user)
    mentioned = get_mentioned_or_replied(update, context)

    if len(mentioned) != 1:
        await msg.reply_text("Использование: /duel @user")
        return

    target = resolve_user_key_from_token(mentioned[0]) if mentioned else None
    if not target and msg.reply_to_message and msg.reply_to_message.from_user:
        target = register_profile(msg.reply_to_message.from_user)
    if not target:
        await msg.reply_text("Не удалось определить пользователя.")
        return
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
        f"{display_user(challenger)} вызывает {display_user(target)} на дуэль!\n"
        "Кликабельные команды:\n"
        "/accept - принять\n"
        "/decline - отклонить"
    )


async def accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    target = register_profile(msg.from_user)

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
        f"Дуэль началась: {display_user(challenger)} vs {display_user(target)}\n"
        f"Первый ход: {display_user(first_turn)}\n"
        f"HP {display_user(challenger)}: 100/100 {hp_bar(100)}\n"
        f"HP {display_user(target)}: 100/100 {hp_bar(100)}\n"
        "Команда выстрела: /shot"
    )


async def shot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    shooter = register_profile(msg.from_user)

    battle_key, duel_state = find_active_duel_for_user(chat_id, shooter)
    if not duel_state:
        await msg.reply_text("У тебя нет активной дуэли. Начни: /duel @user")
        return

    turn = duel_state.get("turn")
    if shooter != turn:
        await msg.reply_text(f"Сейчас ход не твой. Ходит: {display_user(turn)}")
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
        shot_text = f"Попадание! {display_user(shooter)} нанес {damage} урона."
    else:
        shot_text = f"Промах! {display_user(shooter)} не попал."

    duel_state["hp_a"] = hp_a
    duel_state["hp_b"] = hp_b
    duel_state["shots_done"] = shots_done
    duel_state["turn"] = opponent
    active_duels[battle_key] = duel_state

    status_text = (
        f"{shot_text}\n"
        f"Выстрелы: {shots_done}\n"
        f"HP {display_user(a)}: {hp_a}/100 {hp_bar(hp_a)}\n"
        f"HP {display_user(b)}: {hp_b}/100 {hp_bar(hp_b)}"
    )

    # Завершение только если у одного из игроков закончилось HP.
    finished = hp_a <= 0 or hp_b <= 0
    if not finished:
        await reply_game(update, context, status_text + f"\nСледующий ход: {display_user(opponent)}\nКоманда: /shot", 120)
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
        result_text = f"Итог дуэли: победил {display_user(a)}."
    else:
        duel_stats[b]["wins"] += 1
        duel_stats[a]["losses"] += 1
        result_text = f"Итог дуэли: победил {display_user(b)}."

    active_duels.pop(battle_key, None)
    save_json(DUEL_STATS_FILE, duel_stats)
    await reply_game(update, context, status_text + "\n" + result_text + "\nНовая дуэль: /duel @user", 150)


async def decline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    target = register_profile(msg.from_user)

    key, req = find_request_for_target(chat_id, target)
    if not req:
        await msg.reply_text("Для тебя нет активных дуэлей")
        return

    challenger = req["challenger"]
    clear_duel_requests_for_pair(chat_id, challenger, target)
    await msg.reply_text(f"{display_user(target)} отклонил(а) дуэль от {display_user(challenger)}")


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
    mentioned = get_mentioned_or_replied(update, context)
    user = mentioned[0] if mentioned else caller

    ensure_stat_user(duel_stats, user)
    s = duel_stats[user]
    await msg.reply_text(
        f"PvP @{user}\n"
        f"Победы: {s['wins']}\n"
        f"ПоСЂаженёя: {s['losses']}\n"
        f"Рќёчьё: {s['draws']}"
    )


async def pvptop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not duel_stats:
        await msg.reply_text("Пока нет PvP-данных")
        return

    ranking = sorted(
        duel_stats.items(),
        key=lambda item: (item[1].get("wins", 0), -item[1].get("losses", 0)),
        reverse=True,
    )[:10]

    lines = ["Рўоп PvP (по победам):"]
    for i, (user, s) in enumerate(ranking, start=1):
        lines.append(f"{i}. @{user} - W:{s.get('wins', 0)} L:{s.get('losses', 0)} D:{s.get('draws', 0)}")
    await msg.reply_text("\n".join(lines))


async def war(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    attacker = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_or_replied(update, context)

    if len(mentioned) != 1:
        await msg.reply_text("Рспользованёе: /war @user")
        return

    defender = mentioned[0]
    if defender == attacker:
        await msg.reply_text("Рќельзя воевать с собой")
        return

    hp_a = 120
    hp_d = 120
    log_lines = [f"Война: @{attacker} vs @{defender}"]
    for round_num in range(1, 6):
        if hp_a <= 0 or hp_d <= 0:
            break
        dmg_a = random.randint(12, 35)
        dmg_d = random.randint(12, 35)
        hp_d -= dmg_a
        hp_a -= dmg_d
        log_lines.append(
            f"Р аунд {round_num}: @{attacker} -{dmg_a} HP вСЂага, @{defender} -{dmg_d} HP вСЂага | "
            f"HP: {max(hp_a,0)}:{max(hp_d,0)}"
        )

    ensure_stat_user(war_stats, attacker)
    ensure_stat_user(war_stats, defender)

    if hp_a == hp_d:
        war_stats[attacker]["draws"] += 1
        war_stats[defender]["draws"] += 1
        log_lines.append("Ртог: нёчья")
    elif hp_a > hp_d:
        war_stats[attacker]["wins"] += 1
        war_stats[defender]["losses"] += 1
        log_lines.append(f"Ртог: победёл @{attacker}")
    else:
        war_stats[defender]["wins"] += 1
        war_stats[attacker]["losses"] += 1
        log_lines.append(f"Ртог: победёл @{defender}")

    save_json(WAR_STATS_FILE, war_stats)
    await msg.reply_text("\n".join(log_lines))


async def warstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = msg.from_user.username or str(msg.from_user.id)
    mentioned = get_mentioned_or_replied(update, context)
    user = mentioned[0] if mentioned else caller

    ensure_stat_user(war_stats, user)
    s = war_stats[user]
    await msg.reply_text(
        f"Войны @{user}\n"
        f"Победы: {s['wins']}\n"
        f"ПоСЂаженёя: {s['losses']}\n"
        f"Рќёчьё: {s['draws']}"
    )


async def wartop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not war_stats:
        await msg.reply_text("Пока нет данных по войнам")
        return

    ranking = sorted(
        war_stats.items(),
        key=lambda item: (item[1].get("wins", 0), -item[1].get("losses", 0)),
        reverse=True,
    )[:10]

    lines = ["Рўоп войн (по победам):"]
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
        started = parse_iso_date(state.get("started_at"))
        if started and (datetime.now() - started).total_seconds() > 1800:
            state["active"] = False
        else:
            await msg.reply_text("Рейд уже идет. Используйте /raid_hit")
            return

    boss_names = ["Дракон", "Титан", "Лич", "Гидра", "Голем"]
    hp = random.randint(350, 600)
    boss = random.choice(boss_names)
    raid_states[chat_id] = {"active": True, "boss": boss, "hp": hp, "max_hp": hp, "attackers": {}, "started_at": datetime.now().isoformat()}
    save_json(RAID_FILE, raid_states)
    await msg.reply_text(f"Рейд начался!\nБосс: {boss}\nHP: {hp}\nБейте босса: /raid_hit")


async def raid_hit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    state = raid_states.get(chat_id)
    if not state or not state.get("active"):
        await msg.reply_text("Сейчас нет активного рейда. Команда: /raid_start")
        return
    started = parse_iso_date(state.get("started_at"))
    if started and (datetime.now() - started).total_seconds() > 1800:
        state["active"] = False
        save_json(RAID_FILE, raid_states)
        await msg.reply_text("Рейд завершился по времени. Запустите новый: /raid_start")
        return

    user = register_profile(msg.from_user)
    damage = random.randint(25, 85)
    if random.random() < 0.15:
        damage = int(damage * 1.7)

    state["hp"] = max(0, int(state["hp"]) - damage)
    attackers = state.setdefault("attackers", {})
    attackers[user] = int(attackers.get(user, 0)) + damage

    if state["hp"] > 0:
        await msg.reply_text(
            f"{display_user(user)} нанес {damage} урона.\n"
            f"Босс {state['boss']} HP: {state['hp']}/{state['max_hp']}"
        )
        save_json(RAID_FILE, raid_states)
        return

    state["active"] = False
    top = sorted(attackers.items(), key=lambda x: x[1], reverse=True)[:5]
    lines = [f"Босс {state['boss']} повержен!"]
    if top:
        lines.append("Топ урона:")
        for i, (name, dmg) in enumerate(top, start=1):
            lines.append(f"{i}. {display_user(name)}: {dmg}")

    for name, dmg in attackers.items():
        reward = 10 + dmg // 20
        entry = daily_rewards.setdefault(name, {"coins": 0, "streak": 0, "last_claim": ""})
        entry["coins"] = int(entry.get("coins", 0)) + reward

    save_json(RAID_FILE, raid_states)
    save_json(DAILY_FILE, daily_rewards)
    await msg.reply_text("\n".join(lines) + "\nНаграды добавлены в баланс.")


async def raid_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    state = raid_states.get(chat_id)
    if not state or not state.get("active"):
        await msg.reply_text("Активного рейда нет.")
        return

    attackers = state.get("attackers", {})
    top = sorted(attackers.items(), key=lambda x: x[1], reverse=True)[:3]
    text = f"Босс: {state['boss']}\nHP: {state['hp']}/{state['max_hp']}"
    if top:
        text += "\nТоп урона:\n" + "\n".join([f"{i}. {display_user(u)}: {d}" for i, (u, d) in enumerate(top, 1)])
    await msg.reply_text(text)


async def raid_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    ranking = sorted(
        ((u, int(v.get("coins", 0))) for u, v in daily_rewards.items()),
        key=lambda x: x[1],
        reverse=True,
    )[:10]
    if not ranking:
        await msg.reply_text("Пока нет данных.")
        return

    lines = ["Рўоп по монетам:"]
    for i, (u, c) in enumerate(ranking, start=1):
        lines.append(f"{i}. @{u}: {c} монет")
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
    user = register_profile(msg.from_user)
    entry = daily_rewards.setdefault(user, {"coins": 0, "streak": 0, "last_claim": ""})
    today = today_str()
    last = entry.get("last_claim", "")

    if last == today:
        await msg.reply_text(f"Сегодня уже получено.\nБаланс: {entry.get('coins', 0)} монет")
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
    lvl, _, up = grant_xp(user, 12)

    await msg.reply_text(
        f"Ежедневная награда: +{reward}\n"
        f"Серия: {entry['streak']} дней\n"
        f"Баланс: {entry['coins']} монет\n"
        + (f"Новый ранг: {xp_title(lvl)}" if up else "")
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = register_profile(msg.from_user)
    mentioned = get_mentioned_or_replied(update, context)
    user = resolve_user_key_from_token(mentioned[0]) if mentioned else caller
    if not user:
        user = caller
    entry = daily_rewards.setdefault(user, {"coins": 0, "streak": 0, "last_claim": ""})
    await msg.reply_text(
        f"Баланс {display_user(user)}: {entry.get('coins', 0)} монет\n"
        f"Серия daily: {entry.get('streak', 0)}"
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
        await msg.reply_text("Рспользованёе: /buy item_id [кол-во]")
        return

    item_id = context.args[0].lower().strip()
    item = SHOP_ITEMS.get(item_id)
    if not item:
        await msg.reply_text("Рўакого пСЂедмета нет. Спёсок: /shop")
        return

    qty = 1
    if len(context.args) > 1:
        try:
            qty = int(context.args[1])
        except ValueError:
            await msg.reply_text("Колёчество должно быть чёслом")
            return

    if qty < 1 or qty > 99:
        await msg.reply_text("Колёчество: от 1 до 99")
        return

    cost = item["price"] * qty
    wallet = ensure_wallet(user)
    coins = int(wallet.get("coins", 0))
    if coins < cost:
        await msg.reply_text(f"Рќе хватает монет. Рќужно: {cost}, у тебя: {coins}")
        return

    wallet["coins"] = coins - cost
    inv = ensure_inventory(user)
    inv[item_id] = int(inv.get(item_id, 0)) + qty

    save_json(DAILY_FILE, daily_rewards)
    save_json(INVENTORY_FILE, inventories)
    await msg.reply_text(
        f"Покупка успешна: {item['name']} x{qty}\n"
        f"Спёсано: {cost}\n"
        f"Баланс: {wallet['coins']}"
    )


async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    caller = get_user_key(msg.from_user.username, msg.from_user.id)
    mentioned = get_mentioned_or_replied(update, context)
    user = mentioned[0] if mentioned else caller

    inv = ensure_inventory(user)
    if not inv:
        await msg.reply_text(f"РнвентаСЂь @{user} пуст")
        return

    lines = [f"РнвентаСЂь @{user}:"]
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

# === МОПС-ФАРМИЛА ФУНКЦИИ ===

MOPS_GREETINGS = ["Привет! 🐶", "Здорово!", "Приветики!", "Хау!", "Привет, друг!"]
MOPS_FAREWELLS = ["Пока! 🐶", "До свидания!", "Бай!", "Увидимся!"]
MOPS_THANKS = ["Пожалуйста! 🐶", "Рад помочь!", "Обращайся!"]
MOPS_JOKES = ["Почему программист ушёл? Потому что не получил массив.", "Что сказал ноль восьмёрке? Классный ремень!", "Штирлиц выстрелил вслепую. Слепая упала."]
MOPS_QUOTES = ["«Всё будет хорошо» — неизвестный оптимист.", "«Работа не волк, но в лес не убежит».", "«Лучше поздно, чем никогда»."]
MOPS_FACTS = ["Пчёлы умеют различать человеческие лица.", "Осьминоги имеют три сердца.", "Бабочки пробуют вкус ногами.", "Венера вращается в обратную сторону."]
MOPS_COMPLIMENTS = ["Ты молодец! 🌟", "Ты потрясающий! 🔥", "У тебя всё получится! 💪", "Ты лучший! ❤️"]
MOPS_INSULTS = ["Ты как печалька, только хуже.", "Ты конечно молодец, но не очень.", "Твой код — это искусство... неизвестного художника."]
MOPS_8BALL = ["Да", "Нет", "Возможно", "Спроси позже", "Определённо да", "Лучше не надо"]
MOPS_HOROSCOPES = {"aries": "Овен: Сегодня день активных действий!", "taurus": "Телец: Время для отдыха.", "gemini": "Близнецы: Общение принесёт удачу.", "cancer": "Рак: Семья важна сегодня.", "leo": "Лев: Вас ждёт признание!", "virgo": "Дева: Детали решат всё.", "libra": "Весы: Гармония в отношениях.", "scorpio": "Скорпион: Тайны раскроются.", "sagittarius": "Стрелец: Приключения ждут!", "capricorn": "Козерог: Работа принесёт плоды.", "aquarius": "Водолей: Неожиданные идеи.", "pisces": "Рыбы: Творчество на высоте."}
MOPS_WEATHER = ["Солнечно, +25°C ☀️", "Облачно, +18°C ☁️", "Дождь, +15°C 🌧", "Снег, -5°C ❄️"]
HOSHI_TIPS = [
    "Хоши советует: сохраняй монеты в банке, чтобы капали проценты.",
    "Хоши напоминает: ежедневка дает серию, не пропускай.",
    "Хоши: перед дуэлью загляни в инвентарь и купи полезные предметы.",
    "Хоши: квест дня можно закрыть через рыбалку, игры и тренировки.",
]
HOSHI_SCENES = [
    "🐱 Хоши шепчет Мопсу-Фармиле: «Сегодня без багов, только победы». 🐶",
    "🐶 Мопс-Фармила спорит с Хоши, кто быстрее закроет квест дня.",
    "🐱 Хоши принесла удачу в рейд. Мопс-Фармила доволен.",
    "🐶 Мопс-Фармила и 🐱 Хоши устроили мини-совет: «Фармим красиво и честно».",
]

async def mops_greet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_GREETINGS))

async def mops_farewell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_FAREWELLS))

async def mops_thanks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_THANKS))

async def mops_joke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_JOKES))

async def mops_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_QUOTES))

async def mops_fact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_FACTS))

async def mops_compliment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_COMPLIMENTS))

async def mops_insult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(MOPS_INSULTS))

async def mops_8ball(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🔮 {random.choice(MOPS_8BALL)}")

async def mops_coin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🪙 {random.choice(['Орёл', 'Решка'])}")

async def mops_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🎲 Выпало: {random.randint(1, 6)}")

async def mops_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🎲 Число: {random.randint(1, 100)}")

async def mops_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sign = random.choice(list(MOPS_HOROSCOPES.keys()))
    await update.message.reply_text(f"♈️ {sign.capitalize()}: {MOPS_HOROSCOPES[sign]}")

async def mops_weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🌤 {random.choice(MOPS_WEATHER)}")


async def hoshi_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🐱 Хоши умеет:\n"
        "/hoshi_tip — полезный совет\n"
        "/hoshi_on — включить Хоши в чате\n"
        "/hoshi_off — выключить Хоши в чате\n"
        "/hoshi_status — статус Хоши"
    )


async def hoshi_tip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(HOSHI_TIPS))


async def hoshi_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    cfg = ensure_mops_chat(chat_id)
    cfg["hoshi_enabled"] = True
    save_json(MOPS_FILE, mops_state)
    await update.message.reply_text("🐱 Хоши включена для этого чата.")


async def hoshi_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    cfg = ensure_mops_chat(chat_id)
    cfg["hoshi_enabled"] = False
    save_json(MOPS_FILE, mops_state)
    await update.message.reply_text("🐱 Хоши выключена для этого чата.")


async def hoshi_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    cfg = ensure_mops_chat(chat_id)
    st = "включена" if cfg.get("hoshi_enabled", True) else "выключена"
    await update.message.reply_text(f"🐱 Хоши: {st}")

async def mops_love(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"❤️ Любовь: {random.randint(1, 100)}%")


async def gviar_who(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    raw = (msg.text or "").strip()
    question = raw[len("гвиар кто"):].strip(" ?")
    if not question:
        await msg.reply_text("Формат: гвиар кто <вопрос>")
        return
    users = mops_state.setdefault("chat_users", {}).get(chat_id, [])
    if not users:
        await msg.reply_text("Пока мало данных по участникам чата.")
        return
    pool = [u for u in users if u != register_profile(msg.from_user)]
    if not pool:
        pool = users
    picked = random.choice(pool)
    await msg.reply_text(f"Гвиар кто {question}?\nОтвет: {display_user(picked)}")

async def mops_kiss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await rp_action(update, context, "поцеловал(а)", 10)

async def mops_hug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await rp_action(update, context, "обнял(а)", 8)

async def mops_farmila_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🐶 Мопс-Фармила:\n\nПривет: /привет /приветики /хау\nПока: /пока /бай\nСпасибо: /спасибо /благодарю\n\nРазвлечения:\n/шутка /анекдот — шутка\n/цитата — цитата\n/факт — факт\n/комплимент — похвалить\n/оскорбление — поругать\n/шар — да/нет\n/монетка — орел/решка\n/кубик — 1-6\n/число — рандом\n/гороскоп — гороскоп\n\nУправление:\n/mops_on /mops_off /mops_status")

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


async def hoshi_scene_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chats = mops_state.setdefault("chats", {})
    now_hour = datetime.now().strftime("%Y-%m-%d %H")
    for chat_id, cfg in chats.items():
        if not cfg.get("enabled", True):
            continue
        if not cfg.get("hoshi_enabled", True):
            continue
        if cfg.get("last_scene") == now_hour:
            continue
        # Ненавязчиво: с вероятностью 30% раз в час.
        if random.random() > 0.3:
            continue
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=random.choice(HOSHI_SCENES))
            cfg["last_scene"] = now_hour
        except Exception as e:
            logger.warning("Hoshi scene failed for chat %s: %s", chat_id, e)
    save_json(MOPS_FILE, mops_state)


async def bot_added_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    chat_id = str(chat.id)
    ensure_mops_chat(chat_id)
    save_json(MOPS_FILE, mops_state)
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "Привет, я Мопс-Фармила.\n"
                "Коротко: рейды, дуэли, брак/отношения, экономика, мини-игры, квесты, обмен предметами.\n"
                "Быстрый старт: /start\n"
                "Текст-команды тоже работают, например: баланс, браки, рейд, рыбалка."
            ),
        )
    except Exception:
        return


def _user_id_key(user_id: int) -> str:
    return f"id:{user_id}"


def register_profile(tg_user) -> str:
    key = _user_id_key(tg_user.id)
    profile = profiles.setdefault(key, {"username": "", "first_name": ""})
    profile["username"] = (tg_user.username or profile.get("username") or "").lower()
    profile["first_name"] = tg_user.first_name or profile.get("first_name") or ""
    profiles[key] = profile
    save_json(PROFILE_FILE, profiles)
    return key


def display_user(user_key: str) -> str:
    p = profiles.get(user_key, {})
    uname = p.get("username", "")
    return f"@{uname}" if uname else (p.get("first_name") or user_key.replace("id:", "id"))


def ensure_wallet_key(user_key: str) -> dict:
    return daily_rewards.setdefault(user_key, {"coins": 0, "streak": 0, "last_claim": ""})


def resolve_user_key_from_token(token: str) -> str | None:
    token = norm_user(token)
    if token.startswith("id:"):
        return token
    if token.isdigit():
        return f"id:{token}"
    for key, p in profiles.items():
        if norm_user(p.get("username")) == token:
            return key
    return None


def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, seconds: int = 90) -> None:
    if context.job_queue:
        context.job_queue.run_once(
            delete_message_job,
            when=seconds,
            data={"chat_id": chat_id, "message_id": message_id},
            name=f"autodel:{chat_id}:{message_id}",
        )


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    try:
        await context.bot.delete_message(chat_id=data.get("chat_id"), message_id=data.get("message_id"))
    except Exception:
        return


async def reply_game(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, ttl: int = 120) -> None:
    sent = await update.message.reply_text(text)
    schedule_delete(context, sent.chat_id, sent.message_id, ttl)
    schedule_delete(context, update.message.chat_id, update.message.message_id, ttl)


def relation_title(days: int, level: int) -> str:
    if days >= 365 and level >= 18:
        return "Легендарная пара"
    if days >= 180 and level >= 12:
        return "Золотая пара"
    if days >= 90 and level >= 8:
        return "Сильная пара"
    if level >= 5:
        return "Влюбленные"
    return "Новая пара"


async def relation_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    user_key = register_profile(msg.from_user)
    idx, m = find_marriage_for_user(chat_id, user_key)
    if idx is None or not m:
        await msg.reply_text("Вы не в браке.")
        return
    rel_key = marriage_key(chat_id, m["members"][0], m["members"][1])
    rel = relations.setdefault(rel_key, {"xp": 0, "level": 1, "created": datetime.now().isoformat()})
    dt = parse_iso_date(m.get("created")) or datetime.now()
    days = max(1, (datetime.now().date() - dt.date()).days + 1)
    title = relation_title(days, int(rel.get("level", 1)))
    await msg.reply_text(
        f"Статус пары: {title}\n"
        f"Уровень: {rel.get('level', 1)}\n"
        f"XP: {rel.get('xp', 0)}\n"
        f"Вместе: {days} дн."
    )
    save_json(RELATION_FILE, relations)


async def rp_action(update: Update, context: ContextTypes.DEFAULT_TYPE, verb: str, xp: int = 8) -> None:
    msg = update.message
    actor = register_profile(msg.from_user)
    mentioned = get_mentioned_or_replied(update, context)
    target_key = None
    if mentioned:
        target_key = resolve_user_key_from_token(mentioned[0])
    if not target_key and msg.reply_to_message and msg.reply_to_message.from_user:
        target_key = register_profile(msg.reply_to_message.from_user)
    if not target_key:
        await msg.reply_text(f"Использование: {verb} @user или ответом на сообщение.")
        return
    chat_id = str(msg.chat_id)
    idx, m = find_marriage_for_user(chat_id, actor)
    if idx is not None and m and target_key in m.get("members", []):
        rel_key = marriage_key(chat_id, m["members"][0], m["members"][1])
        rel = relations.setdefault(rel_key, {"xp": 0, "level": 1, "created": datetime.now().isoformat()})
        rel["xp"] = int(rel.get("xp", 0)) + xp
        if rel["xp"] >= int(rel.get("level", 1)) * 60:
            rel["xp"] = 0
            rel["level"] = int(rel.get("level", 1)) + 1
        save_json(RELATION_FILE, relations)
    await msg.reply_text(f"{display_user(actor)} {verb} {display_user(target_key)}")


async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    actor = register_profile(msg.from_user)
    mentioned = get_mentioned_or_replied(update, context)
    if len(context.args) < 2:
        await msg.reply_text("Использование: /trade @user item_id [кол-во]")
        return
    target = resolve_user_key_from_token(mentioned[0]) if mentioned else None
    if not target:
        await msg.reply_text("Укажи получателя через @ или ответ.")
        return
    item_id = context.args[-2].lower()
    qty = int(context.args[-1]) if context.args[-1].isdigit() else 1
    inv = ensure_inventory(actor)
    if int(inv.get(item_id, 0)) < qty:
        await msg.reply_text("Не хватает предметов для обмена.")
        return
    tid = f"{msg.chat_id}:{actor}:{target}:{uuid4().hex[:8]}"
    trade_requests[tid] = {"from": actor, "to": target, "item": item_id, "qty": qty, "chat_id": str(msg.chat_id)}
    save_json(TRADE_FILE, trade_requests)
    await msg.reply_text(
        f"Запрос обмена отправлен: {display_user(actor)} -> {display_user(target)}\n"
        f"{item_id} x{qty}\n/accept_trade или /decline_trade"
    )


def _find_trade_for_user(chat_id: str, user_key: str) -> tuple[str, dict] | tuple[None, None]:
    for k, t in trade_requests.items():
        if t.get("chat_id") == chat_id and t.get("to") == user_key:
            return k, t
    return None, None


async def accept_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = register_profile(msg.from_user)
    key, tr = _find_trade_for_user(str(msg.chat_id), user)
    if not tr:
        await msg.reply_text("Нет активных запросов обмена.")
        return
    giver = tr["from"]
    item_id = tr["item"]
    qty = int(tr.get("qty", 1))
    inv_from = ensure_inventory(giver)
    if int(inv_from.get(item_id, 0)) < qty:
        trade_requests.pop(key, None)
        save_json(TRADE_FILE, trade_requests)
        await msg.reply_text("Обмен отменен: у отправителя уже нет предмета.")
        return
    inv_from[item_id] = int(inv_from.get(item_id, 0)) - qty
    if inv_from[item_id] <= 0:
        inv_from.pop(item_id, None)
    inv_to = ensure_inventory(user)
    inv_to[item_id] = int(inv_to.get(item_id, 0)) + qty
    trade_requests.pop(key, None)
    save_json(TRADE_FILE, trade_requests)
    save_json(INVENTORY_FILE, inventories)
    await msg.reply_text(f"Обмен выполнен: {display_user(giver)} передал {item_id} x{qty} для {display_user(user)}")


async def decline_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = register_profile(msg.from_user)
    key, tr = _find_trade_for_user(str(msg.chat_id), user)
    if not tr:
        await msg.reply_text("Нет активных запросов обмена.")
        return
    trade_requests.pop(key, None)
    save_json(TRADE_FILE, trade_requests)
    await msg.reply_text("Обмен отклонен.")


async def mops_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Мини-игры: /polesudes_start, /battleship_start, /mops_guess")


async def mops_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    secret = random.randint(1, 10)
    guess = random.randint(1, 10)
    text = f"Мопс загадал {secret}. Твой бросок: {guess}. " + ("Победа!" if secret == guess else "Почти!")
    await reply_game(update, context, text, 60)


async def polesudes_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    words = ["фармила", "кольцо", "рейд", "бот", "дуэль", "мопс"]
    answer = random.choice(words)
    chat_id = str(update.message.chat_id)
    minigames[chat_id] = {"type": "wheel", "answer": answer, "open": ["_" for _ in answer], "active": True}
    save_json(MINIGAME_FILE, minigames)
    await reply_game(update, context, "Поле чудес началось! Пиши: буква <символ> или слово <слово>", 120)


async def game_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    chat_id = str(msg.chat_id)
    game = minigames.get(chat_id, {})
    if game.get("type") != "wheel" or not game.get("active"):
        return
    text = msg.text.strip().lower()
    if text.startswith("буква "):
        ch = text.split(" ", 1)[1][:1]
        ans = game["answer"]
        open_mask = game["open"]
        hit = False
        for i, c in enumerate(ans):
            if c == ch:
                open_mask[i] = ch
                hit = True
        game["open"] = open_mask
        if "_" not in open_mask:
            game["active"] = False
            await reply_game(update, context, f"Слово разгадано: {ans}", 90)
        else:
            await reply_game(update, context, ("Есть!" if hit else "Нет такой буквы.") + f" {' '.join(open_mask)}", 90)
    elif text.startswith("слово "):
        val = text.split(" ", 1)[1].strip()
        if val == game["answer"]:
            game["active"] = False
            await reply_game(update, context, f"Верно! Слово: {val}", 90)
        else:
            await reply_game(update, context, "Неверно, пробуй еще.", 90)
    minigames[chat_id] = game
    save_json(MINIGAME_FILE, minigames)


async def battleship_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = random.randint(1, 9)
    chat_id = str(update.message.chat_id)
    minigames[chat_id] = {"type": "sea", "target": target, "active": True, "tries": 4}
    save_json(MINIGAME_FILE, minigames)
    await reply_game(update, context, "Морской бой: угадай клетку 1-9 командой: выстрел <число>", 120)


async def sea_shot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = str(msg.chat_id)
    game = minigames.get(chat_id, {})
    if game.get("type") != "sea" or not game.get("active"):
        return
    text = msg.text.strip().lower()
    if not text.startswith("выстрел "):
        return
    part = text.split(" ", 1)[1]
    if not part.isdigit():
        await reply_game(update, context, "Нужно число 1-9.", 60)
        return
    val = int(part)
    game["tries"] = int(game.get("tries", 4)) - 1
    if val == int(game["target"]):
        game["active"] = False
        await reply_game(update, context, "Попадание! Корабль потоплен.", 90)
    elif game["tries"] <= 0:
        game["active"] = False
        await reply_game(update, context, f"Бой окончен. Корабль был в клетке {game['target']}.", 90)
    else:
        await reply_game(update, context, f"Мимо. Осталось попыток: {game['tries']}", 90)
    minigames[chat_id] = game
    save_json(MINIGAME_FILE, minigames)


async def report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await mops_daily_job(context)


async def owner_grant_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_owner(msg.from_user):
        return
    mentioned = get_mentioned_or_replied(update, context)
    target = resolve_user_key_from_token(mentioned[0]) if mentioned else None
    if not target and msg.reply_to_message and msg.reply_to_message.from_user:
        target = register_profile(msg.reply_to_message.from_user)
    if not target:
        await msg.reply_text("Формат: фармила-прем @user")
        return
    p = profiles.setdefault(target, {"username": "", "first_name": ""})
    p["premium"] = True
    profiles[target] = p
    save_json(PROFILE_FILE, profiles)
    await msg.reply_text(f"Премиум Мопса выдан: {display_user(target)}")


async def owner_grant_coins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_owner(msg.from_user):
        return
    parts = msg.text.strip().split()
    if len(parts) < 3:
        await msg.reply_text("Формат: фармила-монеты @user 1000")
        return
    target = resolve_user_key_from_token(parts[1].lstrip("@"))
    if not target:
        await msg.reply_text("Не найден пользователь.")
        return
    try:
        amount = int(parts[2])
    except ValueError:
        await msg.reply_text("Сумма должна быть числом.")
        return
    if amount <= 0:
        await msg.reply_text("Сумма должна быть больше 0.")
        return
    w = ensure_wallet_key(target)
    w["coins"] = int(w.get("coins", 0)) + amount
    daily_rewards[target] = w
    save_json(DAILY_FILE, daily_rewards)
    await msg.reply_text(f"Начислено {amount} монет для {display_user(target)}. Баланс: {w['coins']}")


async def owner_secret_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_owner(msg.from_user):
        return
    # Безопасная версия: фиксируем жалобу в боте и оповещаем чат, без спама в поддержку.
    parts = msg.text.strip().split(maxsplit=2)
    if len(parts) < 2:
        await msg.reply_text("Формат: фармила-жалоба @user причина")
        return
    target = resolve_user_key_from_token(parts[1].lstrip("@"))
    reason = parts[2] if len(parts) > 2 else "подозрение на нарушение"
    if not target:
        await msg.reply_text("Не найден пользователь.")
        return
    chat_id = str(msg.chat_id)
    lst = mod_reports.setdefault(chat_id, [])
    lst.append(
        {
            "target": target,
            "reason": reason,
            "by": register_profile(msg.from_user),
            "created_at": datetime.now().isoformat(),
        }
    )
    save_json(REPORT_FILE, mod_reports)
    await msg.reply_text(
        f"Жалоба записана в журнал модерации:\n"
        f"Пользователь: {display_user(target)}\n"
        f"Причина: {reason}"
    )


async def owner_mod_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_owner(msg.from_user):
        return
    parts = (msg.text or "").split()
    chat_id = str(msg.chat_id)
    cfg = ensure_mod_chat(chat_id)
    if len(parts) < 2:
        await msg.reply_text("фармила-мод статус|вкл|выкл|лимит 7|мут 10|+слово xxx|-слово xxx")
        return
    cmd = parts[1].lower()
    if cmd == "статус":
        await msg.reply_text(
            f"Модерация: {'вкл' if cfg.get('enabled') else 'выкл'}\n"
            f"Лимит флуда: {cfg.get('flood_limit')}/{cfg.get('flood_window_sec')}сек\n"
            f"Мут: {cfg.get('mute_minutes')} мин\n"
            f"Стоп-слов: {len(cfg.get('bad_words', []))}"
        )
    elif cmd == "вкл":
        cfg["enabled"] = True
    elif cmd == "выкл":
        cfg["enabled"] = False
    elif cmd == "лимит" and len(parts) > 2 and parts[2].isdigit():
        cfg["flood_limit"] = max(3, min(20, int(parts[2])))
    elif cmd == "мут" and len(parts) > 2 and parts[2].isdigit():
        cfg["mute_minutes"] = max(1, min(1440, int(parts[2])))
    elif cmd.startswith("+слово") and len(parts) > 2:
        w = parts[2].lower()
        arr = cfg.setdefault("bad_words", [])
        if w not in arr:
            arr.append(w)
    elif cmd.startswith("-слово") and len(parts) > 2:
        w = parts[2].lower()
        cfg["bad_words"] = [x for x in cfg.get("bad_words", []) if x != w]
    mod_state.setdefault("chats", {})[chat_id] = cfg
    save_json(MOD_STATE_FILE, mod_state)
    if cmd != "статус":
        await msg.reply_text("Настройки модерации обновлены.")


async def moderation_guard(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str, user_key: str) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False
    if is_privileged(msg.from_user):
        return False
    cfg = ensure_mod_chat(chat_id)
    if not cfg.get("enabled", True):
        return False

    if user_key in cfg.get("banned", []):
        try:
            await msg.delete()
        except Exception:
            pass
        return True

    text = (msg.text or "").lower()
    warns = cfg.setdefault("warns", {})
    activity = cfg.setdefault("activity", {})
    now_ts = int(datetime.now().timestamp())
    arr = activity.setdefault(user_key, [])
    arr.append(now_ts)
    window = int(cfg.get("flood_window_sec", 12))
    arr = [x for x in arr if now_ts - x <= window]
    activity[user_key] = arr

    bad_hit = any(w in text for w in cfg.get("bad_words", []))
    flood_hit = len(arr) >= int(cfg.get("flood_limit", 6))
    if not bad_hit and not flood_hit:
        mod_state.setdefault("chats", {})[chat_id] = cfg
        save_json(MOD_STATE_FILE, mod_state)
        return False

    warns[user_key] = int(warns.get(user_key, 0)) + 1
    try:
        await msg.delete()
    except Exception:
        pass

    if warns[user_key] >= 3:
        mute_minutes = int(cfg.get("mute_minutes", 10))
        try:
            until = datetime.now() + timedelta(minutes=mute_minutes)
            await context.bot.restrict_chat_member(
                chat_id=msg.chat_id,
                user_id=msg.from_user.id,
                permissions={"can_send_messages": False},
                until_date=until,
            )
        except Exception:
            pass
        warns[user_key] = 0
        try:
            await msg.reply_text(f"{display_user(user_key)} получил авто-мут на {mute_minutes} мин.")
        except Exception:
            pass
    mod_state.setdefault("chats", {})[chat_id] = cfg
    save_json(MOD_STATE_FILE, mod_state)
    return True


def ensure_quest(user_key: str) -> dict:
    q = quests.setdefault(user_key, {"date": "", "target": 5, "progress": 0, "done": False})
    td = today_str()
    if q.get("date") != td:
        q = {"date": td, "target": random.randint(4, 9), "progress": 0, "done": False}
        quests[user_key] = q
    return q


def touch_progress(user_key: str, step: int = 1) -> None:
    q = ensure_quest(user_key)
    if q.get("done"):
        return
    q["progress"] = int(q.get("progress", 0)) + step
    if q["progress"] >= int(q.get("target", 5)):
        q["done"] = True
        wallet = ensure_wallet_key(user_key)
        reward = 120
        wallet["coins"] = int(wallet.get("coins", 0)) + reward
        daily_rewards[user_key] = wallet
        ach = achievements.setdefault(user_key, {"quest_done": 0, "fish": 0, "wins": 0, "lottery": 0})
        ach["quest_done"] = int(ach.get("quest_done", 0)) + 1
    quests[user_key] = q
    save_json(QUEST_FILE, quests)
    save_json(DAILY_FILE, daily_rewards)
    save_json(ACHIEV_FILE, achievements)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    w = ensure_wallet_key(user_key)
    inv = ensure_inventory(user_key)
    q = ensure_quest(user_key)
    ach = achievements.setdefault(user_key, {"quest_done": 0, "fish": 0, "wins": 0, "lottery": 0})
    x = ensure_xp(user_key)
    lvl = int(x.get("level", 1))
    await msg.reply_text(
        f"Профиль {display_user(user_key)}\n"
        f"Монеты: {w.get('coins', 0)}\n"
        f"Ранг: {xp_title(lvl)} ({lvl} ур.)\n"
        f"Инвентарь: {sum(int(v) for v in inv.values())} предметов\n"
        f"Квест дня: {q.get('progress', 0)}/{q.get('target', 5)}\n"
        f"Достижения: квесты {ach.get('quest_done',0)}, рыба {ach.get('fish',0)}, лотерея {ach.get('lottery',0)}"
    )


async def quest_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    q = ensure_quest(user_key)
    await msg.reply_text(
        f"Квест дня: сделать {q['target']} активностей.\n"
        f"Прогресс: {q['progress']}/{q['target']}\n"
        f"Статус: {'выполнен' if q.get('done') else 'в процессе'}"
    )
    save_json(QUEST_FILE, quests)


async def fish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    catches = [
        ("карась", 14, 22),
        ("щука", 26, 42),
        ("сом", 35, 55),
        ("золотая рыбка", 0, 120),
    ]
    name, mn, mx = random.choice(catches)
    reward = random.randint(mn, mx)
    wallet = ensure_wallet_key(user_key)
    wallet["coins"] = int(wallet.get("coins", 0)) + reward
    daily_rewards[user_key] = wallet
    inv = ensure_inventory(user_key)
    fish_key = f"fish_{name.replace(' ', '_')}"
    inv[fish_key] = int(inv.get(fish_key, 0)) + 1
    ach = achievements.setdefault(user_key, {"quest_done": 0, "fish": 0, "wins": 0, "lottery": 0})
    ach["fish"] = int(ach.get("fish", 0)) + 1
    grant_xp(user_key, 7)
    touch_progress(user_key, 1)
    save_json(DAILY_FILE, daily_rewards)
    save_json(INVENTORY_FILE, inventories)
    save_json(ACHIEV_FILE, achievements)
    await reply_game(update, context, f"Улов: {name}. +{reward} монет.", 90)


async def lottery_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    price = 50
    w = ensure_wallet_key(user_key)
    if int(w.get("coins", 0)) < price:
        await msg.reply_text("Не хватает 50 монет на билет.")
        return
    w["coins"] = int(w.get("coins", 0)) - price
    ticket = random.randint(1000, 9999)
    rec = lottery.setdefault(user_key, {"tickets": [], "wins": 0})
    rec["tickets"].append(ticket)
    daily_rewards[user_key] = w
    lottery[user_key] = rec
    touch_progress(user_key, 1)
    save_json(DAILY_FILE, daily_rewards)
    save_json(LOTTERY_FILE, lottery)
    await msg.reply_text(f"Билет куплен: #{ticket}. Розыгрыш: /lottery_draw")


async def lottery_draw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    rec = lottery.setdefault(user_key, {"tickets": [], "wins": 0})
    if not rec.get("tickets"):
        await msg.reply_text("У вас нет билетов.")
        return
    winning = random.randint(1000, 9999)
    prize = 0
    if winning in rec["tickets"]:
        prize = 700
    elif any((t % 100) == (winning % 100) for t in rec["tickets"]):
        prize = 140
    rec["tickets"] = []
    if prize:
        w = ensure_wallet_key(user_key)
        w["coins"] = int(w.get("coins", 0)) + prize
        daily_rewards[user_key] = w
        rec["wins"] = int(rec.get("wins", 0)) + 1
        ach = achievements.setdefault(user_key, {"quest_done": 0, "fish": 0, "wins": 0, "lottery": 0})
        ach["lottery"] = int(ach.get("lottery", 0)) + 1
        save_json(ACHIEV_FILE, achievements)
        await msg.reply_text(f"Выигрыш! Номер #{winning}. Приз: +{prize} монет.")
    else:
        await msg.reply_text(f"Номер #{winning}. В этот раз без приза.")
    lottery[user_key] = rec
    touch_progress(user_key, 1)
    save_json(LOTTERY_FILE, lottery)
    save_json(DAILY_FILE, daily_rewards)


async def mops_train(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    p = profiles.setdefault(user_key, {"username": "", "first_name": ""})
    lvl = int(p.get("mops_level", 1))
    xp = int(p.get("mops_xp", 0)) + random.randint(8, 16)
    need = lvl * 40
    up = False
    if xp >= need:
        xp -= need
        lvl += 1
        up = True
    p["mops_level"] = lvl
    p["mops_xp"] = xp
    profiles[user_key] = p
    grant_xp(user_key, 10)
    touch_progress(user_key, 1)
    save_json(PROFILE_FILE, profiles)
    await msg.reply_text(
        f"Тренировка Мопса завершена.\nУровень: {lvl}\nXP: {xp}/{lvl*40}\n"
        + ("Новый уровень!" if up else "")
    )


async def top_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    ranking = sorted(
        ((u, int(v.get("coins", 0))) for u, v in daily_rewards.items()),
        key=lambda x: x[1],
        reverse=True,
    )[:10]
    if not ranking:
        await msg.reply_text("Топ пока пуст.")
        return
    lines = ["Топ игроков:"]
    for i, (u, c) in enumerate(ranking, 1):
        lines.append(f"{i}. {display_user(u)} - {c} монет")
    await msg.reply_text("\n".join(lines))


def ensure_bank(user_key: str) -> dict:
    return bank_data.setdefault(user_key, {"deposit": 0, "updated": today_str()})


def ensure_xp(user_key: str) -> dict:
    return xp_data.setdefault(user_key, {"xp": 0, "level": 1})


def xp_title(level: int) -> str:
    if level >= 40:
        return "Легенда Фармилы"
    if level >= 30:
        return "Повелитель рейдов"
    if level >= 20:
        return "Элита чата"
    if level >= 12:
        return "Опытный герой"
    if level >= 6:
        return "Боец"
    return "Новичок"


def grant_xp(user_key: str, amount: int) -> tuple[int, int, bool]:
    rec = ensure_xp(user_key)
    lvl = int(rec.get("level", 1))
    xp = int(rec.get("xp", 0)) + max(0, amount)
    need = lvl * 55
    uplevel = False
    while xp >= need:
        xp -= need
        lvl += 1
        need = lvl * 55
        uplevel = True
    rec["xp"] = xp
    rec["level"] = lvl
    xp_data[user_key] = rec
    save_json(XP_FILE, xp_data)
    return lvl, xp, uplevel


async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    rec = ensure_bank(user_key)
    await msg.reply_text(f"Банк {display_user(user_key)}: {rec.get('deposit', 0)} монет")


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("Формат: /deposit 100")
        return
    amount = int(context.args[0])
    if amount <= 0:
        await msg.reply_text("Сумма должна быть больше 0.")
        return
    w = ensure_wallet_key(user_key)
    if int(w.get("coins", 0)) < amount:
        await msg.reply_text("Недостаточно монет.")
        return
    w["coins"] = int(w.get("coins", 0)) - amount
    rec = ensure_bank(user_key)
    rec["deposit"] = int(rec.get("deposit", 0)) + amount
    rec["updated"] = today_str()
    daily_rewards[user_key] = w
    bank_data[user_key] = rec
    save_json(DAILY_FILE, daily_rewards)
    save_json(BANK_FILE, bank_data)
    await msg.reply_text(f"Вклад пополнен на {amount}. В банке: {rec['deposit']}")


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("Формат: /withdraw 100")
        return
    amount = int(context.args[0])
    rec = ensure_bank(user_key)
    if amount <= 0 or int(rec.get("deposit", 0)) < amount:
        await msg.reply_text("Недостаточно средств на вкладе.")
        return
    rec["deposit"] = int(rec.get("deposit", 0)) - amount
    w = ensure_wallet_key(user_key)
    w["coins"] = int(w.get("coins", 0)) + amount
    bank_data[user_key] = rec
    daily_rewards[user_key] = w
    save_json(BANK_FILE, bank_data)
    save_json(DAILY_FILE, daily_rewards)
    await msg.reply_text(f"Снято {amount}. Баланс: {w['coins']}")


async def bank_interest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    changed = False
    td = today_str()
    for user_key, rec in bank_data.items():
        if rec.get("updated") == td:
            continue
        dep = int(rec.get("deposit", 0))
        if dep <= 0:
            rec["updated"] = td
            continue
        add = max(1, dep // 100)
        rec["deposit"] = dep + add
        rec["updated"] = td
        bank_data[user_key] = rec
        changed = True
    if changed:
        save_json(BANK_FILE, bank_data)


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    sender = register_profile(msg.from_user)
    if len(context.args) < 2:
        await msg.reply_text("Формат: /pay @user 100")
        return
    target = resolve_user_key_from_token(context.args[0].lstrip("@"))
    if not target or target == sender:
        await msg.reply_text("Некорректный получатель.")
        return
    if not context.args[1].isdigit():
        await msg.reply_text("Сумма должна быть числом.")
        return
    amount = int(context.args[1])
    if amount <= 0:
        await msg.reply_text("Сумма должна быть больше 0.")
        return
    ws = ensure_wallet_key(sender)
    if int(ws.get("coins", 0)) < amount:
        await msg.reply_text("Недостаточно монет.")
        return
    wt = ensure_wallet_key(target)
    ws["coins"] = int(ws.get("coins", 0)) - amount
    wt["coins"] = int(wt.get("coins", 0)) + amount
    daily_rewards[sender] = ws
    daily_rewards[target] = wt
    save_json(DAILY_FILE, daily_rewards)
    grant_xp(sender, 5)
    await msg.reply_text(f"Перевод выполнен: {display_user(sender)} -> {display_user(target)} : {amount} монет")


async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_key = register_profile(msg.from_user)
    rec = ensure_xp(user_key)
    lvl = int(rec.get("level", 1))
    xp = int(rec.get("xp", 0))
    await msg.reply_text(f"Ранг: {xp_title(lvl)}\nУровень: {lvl}\nXP: {xp}/{lvl*55}")


async def ban_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_privileged(msg.from_user):
        return
    if not context.args:
        await msg.reply_text("Формат: /ban_player @user")
        return
    target = resolve_user_key_from_token(context.args[0].lstrip("@"))
    if not target:
        await msg.reply_text("Пользователь не найден.")
        return
    cfg = ensure_mod_chat(str(msg.chat_id))
    banned = cfg.setdefault("banned", [])
    if target not in banned:
        banned.append(target)
    mod_state.setdefault("chats", {})[str(msg.chat_id)] = cfg
    save_json(MOD_STATE_FILE, mod_state)
    await msg.reply_text(f"{display_user(target)} добавлен в локальный бан-лист бота.")


async def unban_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_privileged(msg.from_user):
        return
    if not context.args:
        await msg.reply_text("Формат: /unban_player @user")
        return
    target = resolve_user_key_from_token(context.args[0].lstrip("@"))
    if not target:
        await msg.reply_text("Пользователь не найден.")
        return
    cfg = ensure_mod_chat(str(msg.chat_id))
    cfg["banned"] = [x for x in cfg.get("banned", []) if x != target]
    mod_state.setdefault("chats", {})[str(msg.chat_id)] = cfg
    save_json(MOD_STATE_FILE, mod_state)
    await msg.reply_text(f"{display_user(target)} удален из локального бан-листа бота.")



def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(ChatMemberHandler(bot_added_greeting, ChatMemberHandler.MY_CHAT_MEMBER))
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
    app.add_handler(CommandHandler("my_rings", my_rings))
    app.add_handler(CommandHandler("myrings", my_rings))
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
    app.add_handler(CommandHandler("trade", trade))
    app.add_handler(CommandHandler("accept_trade", accept_trade))
    app.add_handler(CommandHandler("decline_trade", decline_trade))
    app.add_handler(CommandHandler("relation", relation_status))
    app.add_handler(CommandHandler("relations", relation_status))
    app.add_handler(CommandHandler("mops_play", mops_play))
    app.add_handler(CommandHandler("mops_guess", mops_guess))
    app.add_handler(CommandHandler("polesudes_start", polesudes_start))
    app.add_handler(CommandHandler("battleship_start", battleship_start))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("quest", quest_status))
    app.add_handler(CommandHandler("fish", fish))
    app.add_handler(CommandHandler("lottery_buy", lottery_buy))
    app.add_handler(CommandHandler("lottery_draw", lottery_draw))
    app.add_handler(CommandHandler("mops_train", mops_train))
    app.add_handler(CommandHandler("top_players", top_players))
    app.add_handler(CommandHandler("bank", bank))
    app.add_handler(CommandHandler("deposit", deposit))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("rank", rank))
    app.add_handler(CommandHandler("ban_player", ban_player))
    app.add_handler(CommandHandler("unban_player", unban_player))
    app.add_handler(CommandHandler("mxp", owner_grant_premium))
    app.add_handler(CommandHandler("mxc", owner_grant_coins))
    app.add_handler(CommandHandler("mxr", owner_secret_report))
    app.add_handler(CommandHandler("eco_help", eco_help))
    app.add_handler(CommandHandler("mops_on", mops_on))
    app.add_handler(CommandHandler("mops_off", mops_off))
    app.add_handler(CommandHandler("mops_status", mops_status))
    app.add_handler(CommandHandler("hoshi_help", hoshi_help))
    app.add_handler(CommandHandler("hoshi_tip", hoshi_tip))
    app.add_handler(CommandHandler("hoshi_on", hoshi_on))
    app.add_handler(CommandHandler("hoshi_off", hoshi_off))
    app.add_handler(CommandHandler("hoshi_status", hoshi_status))

    # Мопс-Фармила - основные команды
    app.add_handler(CommandHandler("mops_help", mops_farmila_help))
    app.add_handler(CommandHandler("help_mops", mops_farmila_help))
    app.add_handler(CommandHandler("привет", mops_greet))
    app.add_handler(CommandHandler("приветики", mops_greet))
    app.add_handler(CommandHandler("хау", mops_greet))
    app.add_handler(CommandHandler("пока", mops_farewell))
    app.add_handler(CommandHandler("бай", mops_farewell))
    app.add_handler(CommandHandler("спасибо", mops_thanks))
    app.add_handler(CommandHandler("благодарю", mops_thanks))

    # Развлечения
    app.add_handler(CommandHandler("шутка", mops_joke))
    app.add_handler(CommandHandler("анекдот", mops_joke))
    app.add_handler(CommandHandler("цитата", mops_quote))
    app.add_handler(CommandHandler("факт", mops_fact))
    app.add_handler(CommandHandler("интересное", mops_fact))
    app.add_handler(CommandHandler("комплимент", mops_compliment))
    app.add_handler(CommandHandler("оскорбление", mops_insult))

    # Гадания
    app.add_handler(CommandHandler("шар", mops_8ball))
    app.add_handler(CommandHandler("ball", mops_8ball))
    app.add_handler(CommandHandler("монетка", mops_coin))
    app.add_handler(CommandHandler("орел", mops_coin))
    app.add_handler(CommandHandler("решка", mops_coin))
    app.add_handler(CommandHandler("кубик", mops_dice))
    app.add_handler(CommandHandler("d6", mops_dice))
    app.add_handler(CommandHandler("число", mops_random))
    app.add_handler(CommandHandler("рандом", mops_random))

    # Разное
    app.add_handler(CommandHandler("гороскоп", mops_horoscope))
    app.add_handler(CommandHandler("погода", mops_weather))
    app.add_handler(CommandHandler("love", mops_love))
    app.add_handler(CommandHandler("кто", mops_love))
    app.add_handler(CommandHandler("kiss", mops_kiss))
    app.add_handler(CommandHandler("поцелуй", mops_kiss))
    app.add_handler(CommandHandler("hug", mops_hug))
    app.add_handler(CommandHandler("обнять", mops_hug))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, game_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sea_shot), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, marriage_ceremony_text), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ru_commands), group=1)
    return app


def main() -> None:
    global marriages, duel_stats, war_stats, word_games, raid_states, daily_rewards, inventories, mops_state, profiles, relations, trade_requests, minigames, mod_reports, quests, achievements, lottery, bank_data, xp_data, mod_state
    marriages = load_json(DATA_FILE, {})
    duel_stats = load_json(DUEL_STATS_FILE, {})
    war_stats = load_json(WAR_STATS_FILE, {})
    word_games = load_json(WORD_GAME_FILE, {})
    raid_states = load_json(RAID_FILE, {})
    daily_rewards = load_json(DAILY_FILE, {})
    inventories = load_json(INVENTORY_FILE, {})
    mops_state = load_json(MOPS_FILE, {"chats": {}})
    profiles = load_json(PROFILE_FILE, {})
    relations = load_json(RELATION_FILE, {})
    trade_requests = load_json(TRADE_FILE, {})
    minigames = load_json(MINIGAME_FILE, {})
    mod_reports = load_json(REPORT_FILE, {})
    quests = load_json(QUEST_FILE, {})
    achievements = load_json(ACHIEV_FILE, {})
    lottery = load_json(LOTTERY_FILE, {})
    bank_data = load_json(BANK_FILE, {})
    xp_data = load_json(XP_FILE, {})
    mod_state = load_json(MOD_STATE_FILE, {})

    token = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Set BOT_TOKEN (or TELEGRAM_BOT_TOKEN) in environment")

    app = build_application(token)
    if app.job_queue:
        # Проверяем каждый час, но в чат отправляем только 1 раз в сутки.
        app.job_queue.run_repeating(report_job, interval=3600, first=15, name="mops_daily")
        app.job_queue.run_repeating(bank_interest_job, interval=3600, first=30, name="bank_interest")
        app.job_queue.run_repeating(hoshi_scene_job, interval=3600, first=45, name="hoshi_scene")
    logger.info("Bot is running in polling mode")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


