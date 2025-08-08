import os
import sqlite3
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode, ContentType
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
import asyncio
import pandas as pd
import matplotlib.pyplot as plt
import tempfile
import os


# ===== .env =====
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
PASSWORD = os.getenv("BOT_PASSWORD")

if not API_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file")
if not PASSWORD:
    raise ValueError("BOT_PASSWORD not found in .env file")

# ===== Категории расходов =====
CATEGORIES = [
    "Еда дома", "Еда вне дома", "Одежда и обувь", "Детские товары",
    "Транспорт", "ЖКХ и жильё", "Красота и здоровье", "Развлечения",
    "Подарки", "Техника и гаджеты", "Образование", "Прочее"
]

# ===== Авторизация пользователей =====
AUTHORIZED_USERS = set()

# ===== База данных SQLite =====
def init_db():
    with sqlite3.connect("expenses.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT,
                category TEXT,
                title TEXT,
                amount REAL,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

def add_expense(user: str, category: str, title: str, amount: float):
    with sqlite3.connect("expenses.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO expenses (user, category, title, amount) VALUES (?, ?, ?, ?)",
                       (user, category, title, amount))

def get_report(period: str = "month"):
    with sqlite3.connect("expenses.db") as conn:
        cursor = conn.cursor()
        if period == "day":
            cursor.execute("""
                SELECT user, category, SUM(amount) FROM expenses
                WHERE date >= date('now', 'start of day')
                GROUP BY user, category
            """)
        elif period == "week":
            cursor.execute("""
                SELECT user, category, SUM(amount) FROM expenses
                WHERE date >= date('now', '-6 days')
                GROUP BY user, category
            """)
        else:
            cursor.execute("""
                SELECT user, category, SUM(amount) FROM expenses
                WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
                GROUP BY user, category
            """)
        return cursor.fetchall()

def build_excel_report():
    with sqlite3.connect("expenses.db") as conn:
        df = pd.read_sql_query(
            "SELECT user, category, SUM(amount) as total FROM expenses GROUP BY user, category", conn
        )
        df = df.sort_values(by=["category", "user"])
        filename = os.path.join(tempfile.gettempdir(), "expenses_export.xlsx")
        df.to_excel(filename, index=False)
        return filename


def generate_plot():
    with sqlite3.connect("expenses.db") as conn:
        df = pd.read_sql_query("""
            SELECT user, category, SUM(amount) as total FROM expenses
            WHERE date >= date('now', '-6 days')
            GROUP BY user, category
        """, conn)
        if df.empty:
            return None

        users = df["user"].unique()
        colors = {}
        palette = ["#3B7DDD", "#D95C9B", "#FDBA58", "#8BD17C"]
        for i, user in enumerate(users):
            colors[user] = palette[i % len(palette)]

        df["category"] = df["category"].str.strip()
        pivot = df.pivot(index="category", columns="user", values="total").fillna(0)
        fig, ax = plt.subplots()
        pivot.plot(kind="bar", stacked=True, ax=ax, color=[colors.get(u, "gray") for u in pivot.columns])
        ax.set_title("Расходы за неделю по категориям")
        ax.set_ylabel("Сумма, ₽")
        fig.tight_layout()

        file = os.path.join(os.path.dirname(__file__), "expenses_plot.png")
        fig.savefig(file)
        plt.close(fig)
        return file


# ===== FSM =====
class AuthForm(StatesGroup):
    waiting_for_password = State()

class ExpenseForm(StatesGroup):
    category = State()
    title = State()
    amount = State()

# ===== Хендлеры =====
router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in AUTHORIZED_USERS:
        await state.set_state(AuthForm.waiting_for_password)
        await message.answer("Введите пароль для доступа к боту:")
    else:
        await show_main_menu(message)

@router.message(AuthForm.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    if message.text.strip() == PASSWORD:
        AUTHORIZED_USERS.add(message.from_user.id)
        await state.clear()
        await show_main_menu(message)
    else:
        await message.answer("Неверный пароль. Попробуйте снова.")

async def show_main_menu(message: types.Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Добавить расход")],
            [KeyboardButton(text="Отчет месяц"), KeyboardButton(text="Отчет неделя"), KeyboardButton(text="Отчет день")],
            [KeyboardButton(text="Экспорт в Excel"), KeyboardButton(text="График PNG")],
        ],
        resize_keyboard=True
    )
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=keyboard)

@router.message(F.text == "Добавить расход")
async def handle_add_expense(message: types.Message, state: FSMContext):
    if message.from_user.id not in AUTHORIZED_USERS:
        await message.answer("Вы не авторизованы. Введите /start для начала.")
        return
    kb = ReplyKeyboardBuilder()
    for cat in CATEGORIES:
        kb.add(KeyboardButton(text=cat))
    kb.adjust(2)
    await state.set_state(ExpenseForm.category)
    await message.answer("Выберите категорию:", reply_markup=kb.as_markup(resize_keyboard=True))

@router.message(ExpenseForm.category)
async def process_category(message: types.Message, state: FSMContext):
    if message.text not in CATEGORIES:
        await message.answer("Пожалуйста, выберите категорию только из кнопок.")
        return
    await state.update_data(category=message.text)
    await state.set_state(ExpenseForm.title)
    await message.answer("Введите название покупки:")

@router.message(ExpenseForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(ExpenseForm.amount)
    await message.answer("Введите сумму (без руб):")

@router.message(ExpenseForm.amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Не похоже на число. Проверьте ввод.")
        return
    data = await state.get_data()
    add_expense(message.from_user.first_name, data["category"], data["title"], amount)
    await message.answer(f"Готово! {data['category']} — {data['title']} — {amount:.2f} ₽")
    await state.clear()
    await show_main_menu(message)

@router.message(F.text == "Отчет месяц")
async def report_month(message: types.Message):
    rows = get_report("month")
    if not rows:
        await message.answer("Нет расходов за этот месяц.")
        return
    text = "📊 Расходы за месяц:\n"
    for user, cat, total in rows:
        text += f"{user}: {cat} — {total:.2f} ₽\n"
    await message.answer(text)

@router.message(F.text == "Отчет неделя")
async def report_week(message: types.Message):
    rows = get_report("week")
    if not rows:
        await message.answer("За неделю ещё нет расходов.")
        return
    text = "📊 Расходы за неделю:\n"
    for user, cat, total in rows:
        text += f"{user}: {cat} — {total:.2f} ₽\n"
    await message.answer(text)

@router.message(F.text == "Отчет день")
async def report_day(message: types.Message):
    rows = get_report("day")
    if not rows:
        await message.answer("За сегодня ещё нет расходов.")
        return
    text = "📊 Расходы за сегодня:\n"
    for user, cat, total in rows:
        text += f"{user}: {cat} — {total:.2f} ₽\n"
    await message.answer(text)

@router.message(F.text == "Экспорт в Excel")
async def export_excel_handler(message: types.Message):
    try:
        file = build_excel_report()
        await message.answer_document(FSInputFile(file))
    except Exception as e:
        await message.answer(f"❌ Ошибка при экспорте Excel:\n<code>{e}</code>", parse_mode="HTML")


@router.message(F.text == "График PNG")
async def plot_png_handler(message: types.Message):
    try:
        file = generate_plot()
        if not file:
            await message.answer("Недостаточно данных для графика.")
            return
        await message.answer_photo(FSInputFile(file))
    except Exception as e:
        await message.answer(f"❌ Ошибка при построении графика:\n<code>{e}</code>", parse_mode="HTML")

# ===== MAIN =====
async def main():
    init_db()
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
