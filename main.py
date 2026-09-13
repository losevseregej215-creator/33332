import asyncio
import json
import logging
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "ВАШ_ТОКЕН_СЮДА"
DB_NAME = "dating.db"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            age INTEGER,
            gender TEXT,
            looking_for TEXT,
            city TEXT,
            games TEXT,
            photo_id TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            is_like INTEGER,
            UNIQUE(from_user, to_user)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            file_id TEXT,
            msg_type TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_user(user_id, name, age, gender, looking_for, city, games_list, photo_id=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO users (user_id, name, age, gender, looking_for, city, games, photo_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, name, age, gender, looking_for, city, json.dumps(games_list), photo_id))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id": row[0],
        "name": row[1],
        "age": row[2],
        "gender": row[3],
        "looking_for": row[4],
        "city": row[5],
        "games": json.loads(row[6]) if row[6] else [],
        "photo_id": row[7],
    }


def get_next_profile(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT city, games, looking_for, gender FROM users WHERE user_id = ?", (user_id,))
    me = cur.fetchone()
    if not me:
        conn.close()
        return None

    my_city, my_games_json, my_looking, my_gender = me
    my_games = json.loads(my_games_json) if my_games_json else []

    # Определяем, кого показывать
    if my_looking == "девушку" or my_looking == "девушки":
        target_gender = "девушка"
    elif my_looking == "парня" or my_looking == "парни":
        target_gender = "парень"
    else:
        target_gender = None

    if target_gender:
        cur.execute("""
            SELECT user_id, name, age, city, games, photo_id
            FROM users
            WHERE user_id != ? AND is_active = 1 AND gender = ?
            AND user_id NOT IN (SELECT to_user FROM likes WHERE from_user = ?)
        """, (user_id, target_gender, user_id))
    else:
        cur.execute("""
            SELECT user_id, name, age, city, games, photo_id
            FROM users
            WHERE user_id != ? AND is_active = 1
            AND user_id NOT IN (SELECT to_user FROM likes WHERE from_user = ?)
        """, (user_id, user_id))

    candidates = cur.fetchall()
    conn.close()

    if not candidates:
        return None

    def score(p):
        p_games = json.loads(p[4]) if p[4] else []
        common = len(set(my_games) & set(p_games))
        city_bonus = 100 if p[3] == my_city else 0
        return city_bonus + common

    candidates.sort(key=score, reverse=True)
    b = candidates[0]
    return {
        "user_id": b[0],
        "name": b[1],
        "age": b[2],
        "city": b[3],
        "games": json.loads(b[4]) if b[4] else [],
        "photo_id": b[5],
    }


def add_like(from_user, to_user, is_like):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO likes (from_user, to_user, is_like) VALUES (?, ?, ?)",
        (from_user, to_user, is_like),
    )
    conn.commit()
    conn.close()


def check_match(user_id, target_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM likes WHERE from_user = ? AND to_user = ? AND is_like = 1",
        (target_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def update_photo(user_id, photo_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET photo_id = ? WHERE user_id = ?", (photo_id, user_id))
    conn.commit()
    conn.close()


def update_text(user_id, name, age, city, games_list):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET name = ?, age = ?, city = ?, games = ? WHERE user_id = ?",
        (name, age, city, json.dumps(games_list), user_id),
    )
    conn.commit()
    conn.close()


def save_message(from_user, to_user, file_id, msg_type):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (from_user, to_user, file_id, msg_type) VALUES (?, ?, ?, ?)",
        (from_user, to_user, file_id, msg_type),
    )
    conn.commit()
    conn.close()


# ==================== СОСТОЯНИЯ FSM ====================
class ProfileCreation(StatesGroup):
    name = State()
    age = State()
    gender = State()
    looking_for = State()
    city = State()
    photo = State()
    games = State()


class EditProfile(StatesGroup):
    photo = State()
    text = State()


class SendMessage(StatesGroup):
    waiting = State()


# ==================== КЛАВИАТУРЫ ====================
def main_search_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❤️ Лайк"), KeyboardButton(text="👎 Дизлайк")],
            [KeyboardButton(text="🎤 Отправить голосовое/кружок")],
            [KeyboardButton(text="⚙️ Меню")],
        ],
        resize_keyboard=True,
    )


def menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Смотреть анкеты дальше")],
            [KeyboardButton(text="👤 Моя анкета")],
            [KeyboardButton(text="🚫 Я больше никого не ищу")],
        ],
        resize_keyboard=True,
    )


def my_profile_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Смотреть анкеты")],
            [KeyboardButton(text="🔄 Заполнить анкету заново")],
            [KeyboardButton(text="📷 Изменить фото")],
            [KeyboardButton(text="✏️ Изменить текст анкеты")],
        ],
        resize_keyboard=True,
    )


def gender_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👨 Парень"), KeyboardButton(text="👩 Девушка")],
        ],
        resize_keyboard=True,
    )


def looking_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Девушку"), KeyboardButton(text="Парня")],
            [KeyboardButton(text="Всех")],
        ],
        resize_keyboard=True,
    )


# ==================== СОЗДАНИЕ АНКЕТЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = get_user(message.from_user.id)
    if user:
        await message.answer(
            "Ты уже зарегистрирован! Продолжаем поиск...",
            reply_markup=main_search_kb(),
        )
        await show_next_profile(message.from_user.id, state)
        return
    await message.answer(
        "Привет! Давай создадим анкету.\nКак тебя зовут?",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(ProfileCreation.name)


@dp.message(ProfileCreation.name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Сколько тебе лет?")
    await state.set_state(ProfileCreation.age)


@dp.message(ProfileCreation.age)
async def process_age(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Напиши возраст цифрами.")
        return
    await state.update_data(age=int(message.text))
    await message.answer("Твой пол:", reply_markup=gender_kb())
    await state.set_state(ProfileCreation.gender)


@dp.message(ProfileCreation.gender)
async def process_gender(message: Message, state: FSMContext):
    if "Парень" in message.text:
        gender = "парень"
    elif "Девушка" in message.text:
        gender = "девушка"
    else:
        await message.answer("Выбери из кнопок ниже.")
        return
    await state.update_data(gender=gender)
    await message.answer("Кого ищешь?", reply_markup=looking_kb())
    await state.set_state(ProfileCreation.looking_for)


@dp.message(ProfileCreation.looking_for)
async def process_looking(message: Message, state: FSMContext):
    text = message.text.lower()
    if "девуш" in text:
        looking = "девушку"
    elif "парн" in text:
        looking = "парня"
    elif "всех" in text:
        looking = "всех"
    else:
        await message.answer("Выбери из кнопок ниже.")
        return
    await state.update_data(looking_for=looking)
    await message.answer("Из какого ты города?", reply_markup=ReplyKeyboardRemove())
    await state.set_state(ProfileCreation.city)


@dp.message(ProfileCreation.city)
async def process_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await message.answer("Отправь своё фото:")
    await state.set_state(ProfileCreation.photo)


@dp.message(ProfileCreation.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await message.answer("В какие игры играешь? Перечисли через запятую.\nНапример: Dota 2, CS2, Valorant")
    await state.set_state(ProfileCreation.games)


@dp.message(ProfileCreation.photo)
async def process_photo_invalid(message: Message):
    await message.answer("Нужно отправить именно фото. Попробуй ещё раз.")


@dp.message(ProfileCreation.games)
async def process_games(message: Message, state: FSMContext):
    games_list = [g.strip() for g in message.text.split(",") if g.strip()]
    data = await state.get_data()
    save_user(
        user_id=message.from_user.id,
        name=data["name"],
        age=data["age"],
        gender=data["gender"],
        looking_for=data["looking_for"],
        city=data["city"],
        games_list=games_list,
        photo_id=data["photo_id"],
    )
    await state.clear()
    await message.answer("✅ Анкета готова! Начинаем поиск...", reply_markup=main_search_kb())
    await show_next_profile(message.from_user.id, state)


# ==================== ПОКАЗ АНКЕТ ====================
async def show_next_profile(user_id, state: FSMContext = None):
    profile = get_next_profile(user_id)
    if not profile:
        await bot.send_message(
            user_id,
            "Анкеты закончились. Заходи позже!",
            reply_markup=main_search_kb(),
        )
        return

    text = (
        f"👤 {profile['name']}, {profile['age']}\n"
        f"📍 {profile['city']}\n"
        f"🎮 {', '.join(profile['games']) if profile['games'] else 'не указаны'}"
    )

    if state:
        await state.update_data(current_target=profile["user_id"])

    if profile["photo_id"]:
        await bot.send_photo(
            user_id, profile["photo_id"], caption=text, reply_markup=main_search_kb()
        )
    else:
        await bot.send_message(user_id, text, reply_markup=main_search_kb())


@dp.message(F.text == "❤️ Лайк")
async def like_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("current_target")
    if target:
        add_like(message.from_user.id, target, 1)
        if check_match(message.from_user.id, target):
            me = get_user(message.from_user.id)
            await bot.send_message(
                target,
                f"🎉 У тебя взаимная симпатия с {me['name']}! Напиши ему/ей.",
            )
            await message.answer("🎉 Взаимная симпатия! Можете общаться.")
    await show_next_profile(message.from_user.id, state)


@dp.message(F.text == "👎 Дизлайк")
async def dislike_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("current_target")
    if target:
        add_like(message.from_user.id, target, 0)
    await show_next_profile(message.from_user.id, state)


@dp.message(F.text == "🎤 Отправить голосовое/кружок")
async def voice_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("current_target")
    if not target:
        await message.answer("Сначала выбери анкету.")
        return
    await message.answer(
        "Отправь голосовое сообщение или видеокружок — я перешлю его этому человеку.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(SendMessage.waiting)


@dp.message(SendMessage.waiting, F.voice)
async def send_voice(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("current_target")
    if target:
        save_message(message.from_user.id, target, message.voice.file_id, "voice")
        me = get_user(message.from_user.id)
        try:
            await bot.send_voice(
                target,
                message.voice.file_id,
                caption=f"🎤 Голосовое от {me['name']}",
            )
            await message.answer("✅ Отправлено!", reply_markup=main_search_kb())
        except Exception:
            await message.answer("Не удалось отправить.", reply_markup=main_search_kb())
    await state.set_state(None)


@dp.message(SendMessage.waiting, F.video_note)
async def send_video_note(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get("current_target")
    if target:
        save_message(message.from_user.id, target, message.video_note.file_id, "video_note")
        me = get_user(message.from_user.id)
        try:
            await bot.send_video_note(
                target,
                message.video_note.file_id,
            )
            await bot.send_message(target, f"🎥 Видеокружок от {me['name']}")
            await message.answer("✅ Отправлено!", reply_markup=main_search_kb())
        except Exception:
            await message.answer("Не удалось отправить.", reply_markup=main_search_kb())
    await state.set_state(None)


@dp.message(SendMessage.waiting)
async def send_invalid(message: Message):
    await message.answer("Нужно отправить голосовое или видеокружок.")


# ==================== МЕНЮ ====================
@dp.message(F.text == "⚙️ Меню")
async def menu_handler(message: Message):
    await message.answer("Меню:", reply_markup=menu_kb())


@dp.message(F.text == "🔍 Смотреть анкеты дальше")
async def continue_search(message: Message, state: FSMContext):
    await message.answer("Продолжаем поиск...", reply_markup=main_search_kb())
    await show_next_profile(message.from_user.id, state)


@dp.message(F.text == "🚫 Я больше никого не ищу")
async def stop_search(message: Message):
    await message.answer(
        "Поиск остановлен. Напиши /start, чтобы вернуться.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ==================== МОЯ АНКЕТА ====================
@dp.message(F.text == "👤 Моя анкета")
async def my_profile(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала создай анкету: /start")
        return
    text = (
        f"👤 {user['name']}, {user['age']}\n"
        f"⚧ Пол: {user['gender']}\n"
        f"🔎 Ищу: {user['looking_for']}\n"
        f"📍 {user['city']}\n"
        f"🎮 {', '.join(user['games']) if user['games'] else 'не указаны'}"
    )
    if user["photo_id"]:
        await bot.send_photo(
            message.from_user.id, user["photo_id"], caption=text, reply_markup=my_profile_kb()
        )
    else:
        await message.answer(text, reply_markup=my_profile_kb())


@dp.message(F.text == "🔍 Смотреть анкеты")
async def back_to_search(message: Message, state: FSMContext):
    await message.answer("Продолжаем поиск...", reply_markup=main_search_kb())
    await show_next_profile(message.from_user.id, state)


# ==================== РЕДАКТИРОВАНИЕ ====================
@dp.message(F.text == "🔄 Заполнить анкету заново")
async def restart_profile(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Начнём заново.\nКак тебя зовут?",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(ProfileCreation.name)


@dp.message(F.text == "📷 Изменить фото")
async def change_photo(message: Message, state: FSMContext):
    await message.answer("Отправь новое фото:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(EditProfile.photo)


@dp.message(EditProfile.photo, F.photo)
async def save_new_photo(message: Message, state: FSMContext):
    update_photo(message.from_user.id, message.photo[-1].file_id)
    await state.clear()
    await message.answer("✅ Фото обновлено!", reply_markup=my_profile_kb())


@dp.message(EditProfile.photo)
async def save_new_photo_invalid(message: Message):
    await message.answer("Нужно отправить именно фото.")


@dp.message(F.text == "✏️ Изменить текст анкеты")
async def change_text(message: Message, state: FSMContext):
    await message.answer(
        "Введи новые данные в формате:\nИмя, Возраст, Город, Игры через запятую\n\n"
        "Пример: Иван, 25, Москва, Dota 2, CS2",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(EditProfile.text)


@dp.message(EditProfile.text)
async def save_new_text(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 4 or not parts[1].isdigit():
        await message.answer(
            "Неверный формат. Пример: Иван, 25, Москва, Dota 2, CS2"
        )
        return
    name = parts[0]
    age = int(parts[1])
    city = parts[2]
    games = parts[3:]
    update_text(message.from_user.id, name, age, city, games)
    await state.clear()
    await message.answer("✅ Текст анкеты обновлён!", reply_markup=my_profile_kb())


# ==================== ЗАПУСК ====================
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())