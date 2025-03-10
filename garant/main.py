import logging
import asyncio
import uuid
import random
import string
import re
import json
import aiosqlite

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.filters import Command, CommandObject, Filter
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

from admin.configs import bot_token, reward_ref, min_withdrawal, ton_adress, moderator_ids, gift_reciver, tapping_group_id, support_username, comission, tapping_topic_id, tapping_topic_workers_id, deal_topic_id, withdraw_topic, usdt_trc, usdt_ton, bot_support, suport_id, tapping_group_for_workers, tapping_topic_for_workers_id

# Включаем логирование
logging.basicConfig(level=logging.INFO)

DATABASE = "database.db"

# ----------------------- Работа с базой данных -----------------------

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL,
                wallet TEXT,
                card TEXT,
                referral_code TEXT,
                referrals TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS deals (
                id TEXT PRIMARY KEY,
                seller_id INTEGER,
                buyer_id INTEGER,
                amount REAL,
                currency TEXT,
                description TEXT,
                status TEXT,
                pushed INTEGER,
                transfer_confirmed INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                method TEXT,
                details TEXT,
                completed INTEGER,
                message_id INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS referral_links (
                referral_code TEXT PRIMARY KEY,
                user_id INTEGER
            )
        ''')
        await db.commit()

async def load_users():
    loaded_users = {}
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT user_id, balance, wallet, card, referral_code, referrals FROM users") as cursor:
            async for row in cursor:
                user_id, balance, wallet, card, referral_code, referrals_json = row
                user = User(user_id)
                user.balance = balance
                user.wallet = wallet
                user.card = card
                user.referral_code = referral_code
                user.referrals = json.loads(referrals_json) if referrals_json else []
                loaded_users[user_id] = user
    return loaded_users

async def save_user(user):
    async with aiosqlite.connect(DATABASE) as db:
        referrals_json = json.dumps(user.referrals)
        await db.execute('''
            INSERT OR REPLACE INTO users (user_id, balance, wallet, card, referral_code, referrals)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user.user_id, user.balance, user.wallet, user.card, user.referral_code, referrals_json))
        await db.commit()

async def load_deals():
    loaded_deals = {}
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT id, seller_id, buyer_id, amount, currency, description, status, pushed, transfer_confirmed FROM deals") as cursor:
            async for row in cursor:
                deal_id, seller_id, buyer_id, amount, currency, description, status, pushed, transfer_confirmed = row
                deal = Deal(deal_id, seller_id, amount, currency, description)
                deal.buyer_id = buyer_id
                deal.status = status
                deal.pushed = bool(pushed)
                deal.transfer_confirmed = bool(transfer_confirmed)
                loaded_deals[deal_id] = deal
    return loaded_deals

async def save_deal(deal):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            INSERT OR REPLACE INTO deals (id, seller_id, buyer_id, amount, currency, description, status, pushed, transfer_confirmed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            deal.id,
            deal.seller_id,
            deal.buyer_id,
            deal.amount,
            deal.currency,
            deal.description,
            deal.status,
            int(deal.pushed),
            int(deal.transfer_confirmed)
        ))
        await db.commit()

async def load_withdrawal_requests():
    loaded_requests = {}
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT id, user_id, amount, method, details, completed, message_id FROM withdrawal_requests") as cursor:
            async for row in cursor:
                req_id, user_id, amount, method, details, completed, message_id = row
                req = WithdrawalRequest(user_id, amount, method, details)
                req.id = req_id
                req.completed = bool(completed)
                req.message_id = message_id
                loaded_requests[req_id] = req
    return loaded_requests

async def save_withdrawal_request(request):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            INSERT OR REPLACE INTO withdrawal_requests (id, user_id, amount, method, details, completed, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.id,
            request.user_id,
            request.amount,
            request.method,
            request.details,
            int(request.completed),
            getattr(request, 'message_id', None)
        ))
        await db.commit()

async def load_referral_links():
    links = {}
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT referral_code, user_id FROM referral_links") as cursor:
            async for row in cursor:
                code, user_id = row
                links[code] = user_id
    return links

async def save_referral_link(referral_code, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            INSERT OR REPLACE INTO referral_links (referral_code, user_id)
            VALUES (?, ?)
        ''', (referral_code, user_id))
        await db.commit()

# ----------------------- Основной код бота -----------------------

bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
router = Router()

# Временное хранилище (будет заменено данными из БД при инициализации)
users = {}
referral_links = {}

class User:
    def __init__(self, user_id):
        self.user_id = user_id
        self.balance = 0
        self.referrals = []
        self.wallet = None
        self.card = None
        self.referral_code = str(uuid.uuid4())[:8]
        self.active_deals = []

class Deal:
    def __init__(self, deal_id, seller_id, amount, currency, description):
        self.id = deal_id
        self.seller_id = seller_id
        self.buyer_id = None
        self.amount = amount
        self.currency = currency
        self.description = description
        self.status = "unpaid"
        self.pushed = False
        self.transfer_confirmed = False
        self.seller_deals = len([d for d in deals.values() if d.seller_id == seller_id])

class IsModerator(Filter):
    async def __call__(self, callback: CallbackQuery, bot: Bot, **kwargs) -> bool:
        return callback.from_user.id in moderator_ids

class Form(StatesGroup):
    ton_wallet = State()
    card = State()
    deal_currency = State()
    deal_amount = State()
    deal_description = State()

    dispute_description = State()
    dispute_reply = State()

MIN_AMOUNTS = {
    "RUB": 300,
    "TON": 0.05
}

class WithdrawalRequest:
    def __init__(self, user_id, amount, method, details):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.amount = amount
        self.method = method
        self.details = details
        self.completed = False

withdrawal_requests = {}
deals = {}

def create_inline_keyboard() -> InlineKeyboardMarkup: 
    builder = InlineKeyboardBuilder()
    
    buttons = [
        (f"💎 Привязать TON Кошелек", "bind_ton_wallet"),
        ("💳 Привязать карту", "bind_card"),
        ("🤝 Создать сделку", "create_deal"),
        ("🔗 Реферальная ссылка", "referral_link"),
        ("💖 Комиссия", "commission"),
        ("🛠️ Поддержка", f"tg://resolve?domain={support_username}", True),
        ("👨🏻‍💻 Профиль", "profile")
    ]

    for text, data, *is_url in buttons:
        if is_url:
            builder.button(text=text, url=data)
        else:
            builder.button(text=text, callback_data=data)

    builder.adjust(1, 1, 2, 2, 1)
    return builder.as_markup()

def generate_deal_id():
    random_letters = "".join(random.choice(string.ascii_uppercase) for _ in range(2))
    random_digits = "".join(str(random.randint(0, 9)) for _ in range(8))
    return random_letters + random_digits

def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отменить", callback_data="cancel")
    return builder.as_markup()

def currency_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="RUB 🇷🇺", callback_data="currency_RUB")
    builder.button(text="TON 💎", callback_data="currency_TON")
    builder.button(text="❌ Отменить", callback_data="cancel")
    builder.adjust(2, 1)
    return builder.as_markup()

def referral_keyboard(balance: float) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if balance >= min_withdrawal:
        builder.button(text="📤 Вывести средства", callback_data="withdraw")
    builder.button(text="🔙 Назад", callback_data="back")
    builder.adjust(1)
    return builder.as_markup()

@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if user_id not in users:
        users[user_id] = User(user_id)
        await save_user(users[user_id])
    if command.args:
        args = command.args
        if re.match(r"^[A-Z]{2}\d{8}$", args):
            await handle_deal_start(message, args)
            return
        elif args in referral_links and args != users[user_id].referral_code:
            referrer_id = referral_links[args]
            if user_id not in users[referrer_id].referrals:
                users[referrer_id].referrals.append(user_id)
                users[referrer_id].balance += reward_ref
                await save_user(users[referrer_id])
    await message.answer(
        "<b>🎁 Надежный гарант-сервис подарков</b>\n\n<i>Выберите действие:</i>", reply_markup=create_inline_keyboard()
    )

@router.callback_query(lambda c: c.data in ["bind_ton_wallet", "bind_card"])
async def handle_bind_buttons(callback: CallbackQuery, state: FSMContext):
    user = users[callback.from_user.id]
    await state.update_data(message_id=callback.message.message_id)
    if callback.data == "bind_ton_wallet":
        current = user.wallet or "Пусто"
        await state.set_state(Form.ton_wallet)
        text = f"<b>💎 Ваш текущий кошелек:</b> <code>{current}</code>\n\n<i>Отправьте новый адрес для изменения:</i>"
    else:
        current = user.card or "Пусто"
        await state.set_state(Form.card)
        text = f"<b>💳 Ваша текущая карта:</b> <code>{current}</code>\n\n<i>Отправьте новую карту для изменения:</i>"
    await callback.message.edit_text(
        text,
        reply_markup=cancel_keyboard()
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "cancel")
async def cancel_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>🎁 Надежный гарант-сервис подарков</b>\n\n<i>Выберите действие:</i>",
        reply_markup=create_inline_keyboard()
    )
    await callback.answer()

@router.message(Form.ton_wallet)
async def process_ton_wallet(message: Message, state: FSMContext):
    user = users[message.from_user.id]
    data = await state.get_data()
    try:
        await message.delete()
        if not message.text.startswith("UQ") or len(message.text) != 48:
            raise ValueError("Invalid wallet format")
        user.wallet = message.text
        await save_user(user)
        await bot.edit_message_text(
            chat_id=message.from_user.id,
            message_id=data['message_id'],
            text=f"<b>✅ TON кошелек успешно привязан!</b>\n\n<i>Новый адрес:</i> <code>{message.text}</code>",
            reply_markup=InlineKeyboardBuilder()
                .button(text="◀️ Назад", callback_data="back")
                .as_markup()
        )
        await state.clear()
    except:
        await bot.edit_message_text(
            chat_id=message.from_user.id,
            message_id=data['message_id'],
            text="<i>❌ Неверный формат TON-адреса!</b>\n\n<i>Попробуйте снова:</i>",
            reply_markup=cancel_keyboard()
        )

@router.message(Form.card)
async def process_card(message: Message, state: FSMContext):
    user = users[message.from_user.id]
    data = await state.get_data()
    try:
        await message.delete()
        if not message.text.isdigit() or not (12 <= len(message.text) <= 19):
            raise ValueError("Invalid card format")
        user.card = message.text
        await save_user(user)
        await bot.edit_message_text(
            chat_id=message.from_user.id,
            message_id=data['message_id'],
            text=f"<b>✅ Карта успешно привязана!</b>\n\n<i>Новый номер:</i> <code>{message.text}</code>",
            reply_markup=InlineKeyboardBuilder()
                .button(text="◀️ Назад", callback_data="back")
                .as_markup()
        )
        await state.clear()
    except:
        await bot.edit_message_text(
            chat_id=message.from_user.id,
            message_id=data['message_id'],
            text="<b>❌ Неверный формат карты!</b>\n\n<i>Попробуйте снова:</i>",
            reply_markup=cancel_keyboard()
        )

@router.callback_query(lambda c: c.data == "referral_link")
async def show_referral(callback: CallbackQuery):
    user = users[callback.from_user.id]
    bot_info = await bot.get_me()
    ref_link = f"http://t.me/{bot_info.username}?start={user.referral_code}"
    referral_links[user.referral_code] = user.user_id
    await save_referral_link(user.referral_code, user.user_id)
    stats = (
        f"<b>📊 Статистика:</b>\n"
        f"<i>• Приглашено:</i> <code>{len(user.referrals)}</code>\n"
        f"<i>• Баланс:</i> <code>{user.balance} TON</code>\n\n"
    )
    if user.balance >= min_withdrawal:
        stats += f"<i>✅ Можно вывести:</i> <code>{user.balance} TON</code>"
    else:
        stats += f"<i>🚫 Минимальная сумма вывода:</i> <code>{min_withdrawal} TON</code>"
    await callback.message.edit_text(
        f"<b>🔗 Ваша реферальная ссылка:</b>\n{ref_link}\n\n{stats}",
        reply_markup=referral_keyboard(user.balance)
    )

@router.callback_query(lambda c: c.data == "create_deal")
async def create_deal_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.deal_currency)
    await callback.message.edit_text(
        "<b>💰 Выберите валюту для сделки:</b>",
        reply_markup=currency_keyboard()
    )
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith("currency_"))
async def process_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]
    await state.update_data(
        currency=currency,
        start_message_id=callback.message.message_id
    )
    await state.set_state(Form.deal_amount)
    await bot.edit_message_text(
        chat_id=callback.from_user.id,
        message_id=callback.message.message_id,
        text=f"<b>💰 Введите стоимость сделки в {currency}</b> <i>(минимум {MIN_AMOUNTS[currency]} {currency}):</i>",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()

@router.message(Form.deal_amount)
async def process_deal_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        amount = float(message.text.replace(',', '.'))
        currency = data['currency']
        if amount < MIN_AMOUNTS[currency]:
            raise ValueError
        await bot.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id
        )
        await state.update_data(amount=amount)
        await state.set_state(Form.deal_description)
        await bot.edit_message_text(
            chat_id=message.from_user.id,
            message_id=data['start_message_id'],
            text="<b>🎁 Опишите подарки, которые хотите выставить на продажу:</b>\n\n<i>Пример: 1 PEPE, 3 кепки или 1 Парфюм</i>",
            reply_markup=cancel_keyboard()
        )
    except:
        currency = data.get('currency', 'TON')
        await message.delete()
        await bot.edit_message_text(
            chat_id=message.from_user.id,
            message_id=data['start_message_id'],
            text=f"<b>❌ Неверная сумма!</b>\n<i>Минимальная сумма -</i> <code>{MIN_AMOUNTS[currency]} {currency}</code>\n\n<i>Попробуйте снова:</i>",
            reply_markup=cancel_keyboard()
        )

@router.message(Form.deal_description)
async def process_deal_description(message: Message, state: FSMContext):
    data = await state.get_data()
    await bot.delete_message(
        chat_id=message.chat.id,
        message_id=message.message_id
    )
    deal_id = generate_deal_id()
    bot_info = await bot.get_me()
    
    deal = Deal(
        deal_id=deal_id,
        seller_id=message.from_user.id,
        amount=data['amount'],
        currency=data['currency'],
        description=message.text
    )
    deals[deal_id] = deal
    await save_deal(deal)
    
    # Клавиатура с кнопкой спора для продавца
    seller_kb = InlineKeyboardBuilder()
    seller_kb.button(text="⚠️ Открыть спор", callback_data=f"open_dispute_{deal.id}")
    seller_kb.adjust(1)
    
    text = (
        f"<b>✅ Сделка #{deal_id} успешно создана!</b>\n\n"
        f"Стоимость: <code>{data['amount']} {data['currency']}</code>\n"
        f"Описание: {message.text}\n\n"
        f"Команда для покупателя: <code>/connect_to_deal {deal_id}</code>\n"
        f"Ссылка: http://t.me/{bot_info.username}?start={deal_id}\n\n"
        f"<i><b>⚠️ Никогда не отправляйте</b> подарки на стронние аккаунты покупателя, только на тот, <b>что указан в сделке.</b></i>"
    )
    
    seller_first_name = message.from_user.first_name
    
    text_for_workers = (
        f"<b>✅ Сделка #{deal_id} успешно создана!</b>\n\n"
        f"Стоимость: <code>{data['amount']} {data['currency']}</code>\n"
        f"Описание: {message.text}\n\n"
        f"Продавец: {seller_first_name}"
    )
    
    await bot.edit_message_text(
        chat_id=message.from_user.id,
        message_id=data['start_message_id'],
        text=text,
        reply_markup=seller_kb.as_markup()
    )
    await bot.send_message(chat_id=tapping_group_for_workers, message_thread_id=tapping_topic_for_workers_id, text=text_for_workers)
    await state.clear()
    
async def handle_deal_start(message: Message, deal_id: str):
    user_id = message.from_user.id
    if deal_id in deals:
        deal = deals[deal_id]
        if user_id == deal.seller_id:
            await message.answer("<b>❌ Вы не можете участвовать в своей собственной сделке!</b>")
            return
        if deal.buyer_id is not None:
            if user_id == deal.buyer_id:
                await message.answer("<b>✅ Вы уже присоединены к этой сделке!</b>")
            else:
                await message.answer("<b>❌ В сделке уже участвует другой покупатель!</b>")
            return
        deal.buyer_id = user_id
        bot_info = await bot.get_me()
        seller = await bot.get_chat(deal.seller_id)
        buyer_kb = InlineKeyboardBuilder()
        buyer_kb.button(text="🔄 Проверить статус платежа", callback_data=f"check_payment_{deal.id}")
        buyer_kb.button(text="⚠️ Открыть спор", callback_data=f"open_dispute_{deal.id}")
        buyer_kb.adjust(1)
        text = (
                f"<b>Сделка #{deal.id} 🚀</b>\n\n"
                f"<b>Продавец:</b> {seller.first_name}\n"
                f"<b>🤝 Успешные сделки продавца</b> - <code>{deal.seller_deals}</code>\n\n"
                f"<b>💸 Адрес для оплаты:</b>\n"
                f"<b>TON -</b> <code>{ton_adress}</code>\n"
                f"<b>USDT [TRC 20] -</b> <code>{usdt_trc}</code>\n"
                f"<b>USDT [TON] -</b> <code>{usdt_ton}</code>\n"
                f"<b>✉️ Комментарий к платежу:</b> <code>{deal.id}</code>\n\n"
                f"<b>- Для оплаты по банковской карте обратитесь в поддержку.</b>\n\n"
                f"<b>Сумма к оплате:</b> <code>{deal.amount} {deal.currency}</code>\n"
                f"<b>Вы покупаете:</b> <code>{deal.description}</code>\n\n"
                f"<i><b>⚠️ Пожалуйста, убедитесь в том,</b> что вы абсолютно правильно ввели коментарий перед тем как отправить оплату. <b>Коментарий обязателен.</b></i>"
        )
        await message.answer(text, reply_markup=buyer_kb.as_markup())
        buyer = await bot.get_chat(user_id)
        seller_text = (
            f"<b>👤 Покупатель {buyer.first_name} </b>"
            f"<b>присоединился к сделке #{deal.id}</b>"
        )
        await bot.send_message(deal.seller_id, seller_text)
    else:
        await message.answer("<b>❌ Сделка не найдена!</b>")

async def show_deal_message(user_id: int, deal_id: str):
    deal = deals.get(deal_id)
    if not deal:
        return await bot.send_message(user_id, "<b>❌ Сделка не найдена!</b>")
    
    seller = await bot.get_chat(deal.seller_id)
    kb = InlineKeyboardBuilder()
    
    # Кнопки для покупателя и продавца
    if user_id == deal.buyer_id or user_id == deal.seller_id:
        kb.button(text="🔄 Проверить статус платежа", callback_data=f"check_payment_{deal.id}")
        kb.button(text="⚠️ Открыть спор", callback_data=f"open_dispute_{deal.id}")
        kb.adjust(1)
    
    text = (
        f"<b>Сделка #{deal.id} 🚀</b>\n\n"
        f"<b>Продавец:</b> {seller.first_name}\n"
        f"<b>🤝 Успешные сделки продавца</b> - <code>{deal.seller_deals}</code>\n\n"
        f"<b>💸 Адрес для оплаты:</b>\n"
        f"<b>TON -</b> <code>{ton_adress}</code>\n"
        f"<b>USDT [TRC 20] -</b> <code>{usdt_trc}</code>\n"
        f"<b>USDT [TON] -</b> <code>{usdt_ton}</code>\n"
        f"<b>✉️ Комментарий к платежу:</b> <code>{deal.id}</code>\n\n"
        f"<b>- Для оплаты по банковской карте обратитесь в поддержку.</b>\n\n"
        f"<b>Сумма к оплате:</b> <code>{deal.amount} {deal.currency}</code>\n"
        f"<b>Вы покупаете:</b> <code>{deal.description}</code>\n\n"
        f"<i><b>⚠️ Пожалуйста, убедитесь в том,</b> что вы абсолютно правильно ввели коментарий перед тем как отправить оплату. <b>Коментарий обязателен.</b></i>"
    )
    
    await bot.send_message(
        chat_id=user_id,
        text=text,
        reply_markup=kb.as_markup() if user_id in [deal.buyer_id, deal.seller_id] else None
    )

@router.callback_query(lambda c: c.data.startswith("open_dispute_"))
async def open_dispute(callback: CallbackQuery, state: FSMContext):
    deal_id = callback.data.split("_")[-1]
    deal = deals.get(deal_id)
    
    if not deal:
        return await callback.answer("❌ Сделка не найдена!", show_alert=True)
    
    # Проверяем, является ли пользователь покупателем или продавцом
    if callback.from_user.id not in [deal.buyer_id, deal.seller_id]:
        return await callback.answer("❌ Только участники сделки могут открыть спор!", show_alert=True)
    
    await state.update_data(deal_id=deal_id, dispute_message_id=callback.message.message_id)
    await state.set_state(Form.dispute_description)
    
    await callback.message.edit_text(
        text="<b>⚠️ Опишите проблему подробно:</b>",
        reply_markup=InlineKeyboardBuilder()
            .button(text="❌ Отменить", callback_data="cancel_dispute")
            .as_markup()
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "cancel_dispute")
async def cancel_dispute(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    
    # Редактируем сообщение с запросом описания проблемы
    await callback.message.edit_text(
        text="<b>❌ Открытие спора отменено</b>",
        reply_markup=None  # Убираем клавиатуру
    )
    
    # Возвращаем пользователя к сообщению со сделкой
    if 'deal_id' in data:
        await show_deal_message(callback.from_user.id, data['deal_id'])
    await callback.answer()

@router.message(Form.dispute_description)
async def process_dispute_description(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        # Удаляем сообщение пользователя с описанием
        await message.delete()
        
        # Отправляем уведомление о принятии спора
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>⏳ Ваш спор принят в обработку. Ожидайте ответа модератора.</b>"
        )
        
        # Получаем данные о сделке
        deal = deals[data['deal_id']]
        
        # Проверяем корректность идентификаторов
        if not isinstance(deal.seller_id, int) or not isinstance(deal.buyer_id, int):
            raise ValueError("Некорректные идентификаторы пользователей")
        
        # Получаем информацию о продавце и покупателе
        seller = await bot.get_chat(deal.seller_id)
        buyer = await bot.get_chat(deal.buyer_id)
        
        # Формируем текст для модераторов
        moderator_text = (
            f"<b>🚨 Новый спор по сделке #{deal.id}</b>\n\n"
            f"<b>👤 Покупатель:</b> @{buyer.username or buyer.first_name} (ID: {buyer.id})\n"
            f"<b>👤 Продавец:</b> @{seller.username or seller.first_name} (ID: {seller.id})\n\n"
            f"<b>📝 Описание проблемы:</b>\n<i>{message.text}</i>"
        )
        
        # Создаем клавиатуру для модераторов
        moderator_kb = InlineKeyboardBuilder()
        moderator_kb.button(
            text="📩 Ответить", 
            callback_data=f"reply_dispute_{message.from_user.id}_{data['deal_id']}"
        )
        
        # Отправляем сообщение модераторам
        await bot.send_message(
            chat_id=tapping_group_id, message_thread_id=deal_topic_id,
            text=moderator_text,
            reply_markup=moderator_kb.as_markup()
        )
        
    except Exception as e:
        logging.error(f"Dispute error: {e}")

# 6. Обработчик ответа модератора
@router.callback_query(lambda c: c.data.startswith("reply_dispute_"), IsModerator())
async def reply_dispute(callback: CallbackQuery, state: FSMContext):
    # Разделяем данные, игнорируя первые 2 элемента ("reply" и "dispute")
    parts = callback.data.split("_")[2:]
    if len(parts) < 2:
        return
    
    # Первый элемент - user_id, остальные - deal_id (объединяем через _)
    user_id = parts[0]
    deal_id = "_".join(parts[1:])
    
    await state.update_data(
        target_user=int(user_id),
        deal_id=deal_id,
        dispute_msg_id=callback.message.message_id
    )
    await state.set_state(Form.dispute_reply)
    
    await callback.message.edit_text(
        "<b>✍️ Введите ответ для пользователя:</b>",
        reply_markup=InlineKeyboardBuilder()
            .button(text="❌ Отменить", callback_data="cancel_reply")
            .as_markup()
    )

# 7. Обработчик ответа на спор
@router.message(Form.dispute_reply)
async def process_dispute_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        # Удаляем сообщение с ответом модератора
        await message.delete()
        
        # Отправляем ответ пользователю
        await bot.send_message(
            chat_id=data['target_user'],
            text=f"<b>📩 Ответ по спору #{data['deal_id']}:</b>\n\n<i>{message.text}</i>"
        )
        
        # Редактируем сообщение модератора
        await bot.edit_message_text(
            chat_id=tapping_group_id, message_thread_id=deal_topic_id,
            message_id=data['dispute_msg_id'],
            text=f"<b>✅ Ответ отправлен пользователю {data['target_user']}</b>\n"
                 f"<b>По спору #{data['deal_id']}\n\n</b>"
                 f"<b>Текст ответа:</b> <i>{message.text}</i>",
            reply_markup=None
        )
        
    except Exception as e:
        logging.error(f"Ошибка ответа на спор: {e}")
    finally:
        await state.clear()

@router.callback_query(lambda c: c.data.startswith("check_payment_"))
async def check_payment(callback: CallbackQuery):
    deal_id = callback.data.split("_")[-1]
    deal = deals.get(deal_id)
    if not deal:
        return await callback.answer("❌ Сделка не найдена!", show_alert=True)
    buyer = await bot.get_chat(callback.from_user.id)
    if deal.status == "unpaid":
        deal.status = "pending"
        seller = await bot.get_chat(deal.seller_id)
        moderator_kb = InlineKeyboardBuilder()
        moderator_kb.button(
            text="✅ Подтвердить платеж", 
            callback_data=f"push_deal_{deal.id}"
        )
        moderator_text = (
            "<b>🔔 Требуется проверка платежа!</b>\n\n"
            f"<b>📝 Сделка ID:</b> <code>{deal.id}</code>\n"
            f"<b>💵 Сумма:</b> <code>{deal.amount} {deal.currency}</code>\n"
            f"<b>📦 Подарка:</b> {deal.description}\n\n"
            f"<b>👤 Продавец:</b>\n"
            f"<b>ID:</b> <code>{seller.id}</code>\n"
            f"<b>Username:</b> @{seller.username if seller.username else 'нет'}\n\n"
            f"<b>👥 Покупатель:</b>\n"
            f"<b>ID:</b> {buyer.id}\n"
            f"<b>Username:</b> @{buyer.username if buyer.username else 'нет'}"
        )
        await bot.send_message(
            chat_id=tapping_group_id, message_thread_id=tapping_topic_id,
            text=moderator_text,
            reply_markup=moderator_kb.as_markup()
        )
     
    status_text = "Не оплачено ❌" if deal.status == "unpaid" else "Проверяется ⏳"
    await callback.answer(status_text, show_alert=True)

@router.callback_query(lambda c: c.data.startswith("push_deal_"), IsModerator())
async def push_deal(callback: CallbackQuery):
    deal_id = callback.data.split("_")[-1]
    deal = deals.get(deal_id)
    
    if deal and not deal.pushed:
        deal.pushed = True
        # Сохраняем изменения после установки флага pushed
        await save_deal(deal)
        
        seller = await bot.get_chat(deal.seller_id)
        seller_kb = InlineKeyboardBuilder()
        seller_kb.button(
            text="✅ Подтвердить передачу товара", 
            callback_data=f"confirm_transfer_{deal.id}"
        )
        seller_text = (
            f"<b>💰 Покупатель оплатил заказ по сделке #{deal.id}!</b>\n\n"
            f"<b>➡️ Передайте подарки получателю:</b> @{gift_reciver}\n"
            f"<b>🎁 Описание подарков:</b> {deal.description}\n\n"
            f"<b>После передачи подарков</b> нажмите кнопку ниже, чтобы <b>завершить сделку.</b>"
        )
        await bot.send_message(
            chat_id=deal.seller_id,
            text=seller_text,
            reply_markup=seller_kb.as_markup()
        )
        
        await callback.message.edit_text(
            f"<b>✅ Пуш отправлен продавцу по сделке #{deal.id}</b>",
            reply_markup=None
        )
        
        await bot.send_message(
            chat_id=tapping_group_for_workers, 
            message_thread_id=tapping_topic_for_workers_id, 
            text=f"<b>✅ Пуш отправлен продавцу по сделке #{deal.id}</b>"
        )
        
        await callback.answer()
    else:
        await callback.answer("❌ Сделка уже обработана!", show_alert=True)

@router.callback_query(lambda c: c.data.startswith("confirm_transfer_"))
async def confirm_transfer(callback: CallbackQuery):
    deal_id = callback.data.split("_")[-1]
    deal = deals.get(deal_id)
    
    if deal and not deal.transfer_confirmed:
        await asyncio.sleep(random.uniform(4, 7))
        deal.transfer_confirmed = True
        # Сохраняем обновлённое состояние сделки в БД
        await save_deal(deal)
        await callback.answer("🕒 Подарки не найдены. Повторите попытку через 3 минуты.")
    else:
        await asyncio.sleep(random.uniform(4, 7))
        await callback.answer("Попробуйте позже!")

@router.message(Command("connect_to_deal"))
async def connect_to_deal(message: Message, command: CommandObject):
    if command.args:
        deal_id = command.args
        await handle_deal_start(message, deal_id)
    else:
        await message.answer("<b>❌ Укажите ID сделки:</b> <code>/connect_to_deal ID_СДЕЛКИ</code>")

@router.callback_query(lambda c: c.data == "profile")
async def show_profile(callback: CallbackQuery):
    user = users[callback.from_user.id]
    completed_deals = [
        deal for deal in deals.values() 
        if deal.seller_id == user.user_id and deal.transfer_confirmed
    ]
    total_completed = len(completed_deals)
    total_amount = sum(deal.amount for deal in completed_deals)
    profile_text = (
        "<b>👤 Ваш профиль:</b>\n\n"
        f"✅ <i>Успешных сделок:</i> {total_completed}\n"
        f"💰 <i>Общая сумма:</i> {total_amount:.2f} TON\n\n"
        f"💼 <i>TON кошелек:</i> <code>{user.wallet or 'Пусто'}</code>\n"
        f"💳 <i>Привязанная карта:</i> <code>{user.card or 'Пусто'}</code>"
    )
    await callback.message.edit_text(
        profile_text, parse_mode='HTML',
        reply_markup=InlineKeyboardBuilder()
            .button(text="◀️ Назад", callback_data="back")
            .as_markup()
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "commission")
async def show_commission(callback: CallbackQuery):
    commission_text = (
        "<b>💼 Комиссия сервиса</b>\n\n"
        f"• <i>Комиссия:</i> {comission}%\n"
        "• Вы не делите комиссию с продавцом.\n\n"
        "<i>Комиссия взимается только с суммы сделки.</i>"
    )
    await callback.message.edit_text(
        commission_text, parse_mode='HTML',
        reply_markup=InlineKeyboardBuilder()
            .button(text="◀️ Назад", callback_data="back")
            .as_markup()
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "withdraw")
async def withdraw_funds(callback: CallbackQuery):
    user = users[callback.from_user.id]
    if not user.wallet and not user.card:
        await callback.answer("❌ Привяжите кошелек или карту!", show_alert=True)
        return
    if user.balance < min_withdrawal:
        await callback.answer(f"❌ Минимальная сумма вывода: {min_withdrawal} TON", show_alert=True)
        return
    method = "TON Кошелек" if user.wallet else "Карта"
    details = user.wallet if user.wallet else user.card
    request = WithdrawalRequest(
        user_id=user.user_id,
        amount=user.balance,
        method=method,
        details=details
    )
    withdrawal_requests[request.id] = request
    moderator_kb = InlineKeyboardBuilder()
    moderator_kb.button(
        text="✅ Отправил", 
        callback_data=f"confirm_withdraw_{request.id}"
    )
    user_profile = await bot.get_chat(user.user_id)
    moderator_text = (
        "<b>🔄 Запрос на вывод:</b>\n\n"
        f"👤 <i>Пользователь:</i> @{user_profile.username or 'N/A'} (ID: {user.user_id})\n"
        f"💵 <i>Сумма:</i> <code>{user.balance} TON</code>\n"
        f"📦 <i>Метод:</i> <code>{method}</code>\n"
        f"🔗 <i>Данные:</i> <code>{details}</code>"
    )
    sent_message = await bot.send_message(
        chat_id=tapping_group_id, message_thread_id=withdraw_topic,
        text=moderator_text, parse_mode='HTML',
        reply_markup=moderator_kb.as_markup()
    )
    request.message_id = sent_message.message_id
    await save_withdrawal_request(request)
    await callback.message.edit_text(
        "<b>✅ Запрос на вывод средств отправлен на модерацию!</b>\n\n"
        "<i>Ожидайте подтверждения от модератора.</i>",
        reply_markup=InlineKeyboardBuilder()
            .button(text="◀️ Назад", callback_data="back")
            .as_markup()
    )
    await callback.answer("✅ Запрос отправлен на модерацию!", show_alert=True)
    user.balance = 0
    await save_user(user)

@router.callback_query(lambda c: c.data.startswith("confirm_withdraw_"), IsModerator())
async def confirm_withdrawal(callback: CallbackQuery):
    request_id = callback.data.split("_")[-1]
    request = withdrawal_requests.get(request_id)
    if request and not request.completed:
        request.completed = True
        user = users[request.user_id]
        success_text = (
            f"✅ <b>Перевод на сумму</b> <code>{request.amount} TON</code>\n"
            f"<b>успешно отправлен на</b> <code>{request.details}</code>"
        )
        await bot.send_message(
            chat_id=request.user_id,
            text=success_text, parse_mode='HTML'
        )
        await callback.message.edit_text(
            f"<b>✅ Вывод подтвержден</b>\n{callback.message.text}",
            reply_markup=None
        )
        await callback.answer()
    else:
        await callback.answer("❌ Запрос уже обработан!", show_alert=True)

@router.callback_query(lambda c: c.data == "back")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>🎁 Надежный гарант-сервис подарков</b>\n\n<i>Выберите действие:</i>",
        reply_markup=create_inline_keyboard()
    )

# Добавить в классы состояний (StatesGroup)
class AdminForm(StatesGroup):
    broadcast = State()
    increment_user = State()
    decrement_user = State()  # Новое состояние для уменьшения суммы
    user_id = State()
    amount = State()

# Добавить новую клавиатуру для админ-панели
def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        ("📊 Статистика", "admin_stats"),
        ("📈 Увеличить сделки", "admin_increment"),
        ("📉 Уменьшить сделки", "admin_decrement"),  # Новая кнопка
        ("📤 Рассылка", "admin_broadcast"),
        ("◀️ Назад", "back")
    ]
    for text, data in buttons:
        builder.button(text=text, callback_data=data)
    builder.adjust(1)
    return builder.as_markup()

# Добавить обработчик команды /admin
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in moderator_ids:
        return
    await message.answer(
        "<b>⚙️ Админ-панель:</b>",
        reply_markup=admin_keyboard()
    )

# Обработчики для обеих операций (увеличение и уменьшение)
@router.callback_query(lambda c: c.data in ["admin_increment", "admin_decrement"])
async def admin_change_balance_start(callback: CallbackQuery, state: FSMContext):
    operation_type = callback.data  # "admin_increment" или "admin_decrement"
    
    sent_message = await callback.message.edit_text(
        "<b>Введите ID пользователя:</b>",
        reply_markup=cancel_keyboard()
    )
    
    await state.update_data(
        sent_message_id=sent_message.message_id,
        operation_type=operation_type  # Сохраняем тип операции
    )
    await state.set_state(AdminForm.user_id)
    await callback.answer()

# Обработчик ввода user_id
@router.message(AdminForm.user_id)
async def process_user_id(message: Message, state: FSMContext):
    try:
        await message.delete()
        user_id = int(message.text)
        
        if user_id not in users:
            raise ValueError("Пользователь не найден")
            
        # Запрашиваем сумму
        sent_message = await message.answer(
            "<b>Введите сумму:</b>",
            reply_markup=cancel_keyboard()
        )
        
        await state.update_data(
            user_id=user_id,
            sent_message_id=sent_message.message_id
        )
        await state.set_state(AdminForm.amount)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

# Обработчик ввода суммы
@router.message(AdminForm.amount)
async def process_amount(message: Message, state: FSMContext):
    try:
        await message.delete()
        data = await state.get_data()
        
        # Получаем сохраненные данные
        user_id = data['user_id']
        operation_type = data['operation_type']
        amount = float(message.text)
        
        user = users[user_id]
        
        # Определяем тип операции
        if operation_type == "admin_increment":
            deal_type = "Админское увеличение"
            amount_abs = amount
        else:
            deal_type = "Админское уменьшение"
            amount_abs = -amount
            
        # Создаем сделку
        deal_id = generate_deal_id()
        new_deal = Deal(
            id=deal_id,
            user_id=user.user_id,
            amount=amount_abs,
            currency="TON",
            description=deal_type
        )
        new_deal.transfer_confirmed = True
        deals[deal_id] = new_deal
        await save_deal(new_deal)
        
        # Редактируем сообщение с результатом
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=data["sent_message_id"],
            text=f"<b>✅ Изменено</b> <code>{amount} TON</code> "
                 f"<b>в статистике пользователя</b> <code>{user_id}</code>",
            reply_markup=InlineKeyboardBuilder()
                .button(text="◀️ Назад", callback_data="admin_back")
                .as_markup()
        )
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@router.message(AdminForm.user_id)
async def process_user_id(message: Message, state: FSMContext):
    data = await state.get_data()
    message_id = data.get("sent_message_id")
    try:
        await message.delete()
        user_id = int(message.text)
        if user_id not in users:
            raise ValueError
        await state.update_data(user_id=user_id)
        await state.set_state(AdminForm.amount)
        # Редактируем предыдущее бот-сообщение, чтобы заменить текст запроса
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_id,
            text="<b>Введите сумму для изменения:</b>",  # Общий текст для увеличения/уменьшения
            reply_markup=cancel_keyboard()
        )
    except Exception as e:
        await message.answer("❌ Неверный ID пользователя!")

# Обработчик для кнопки "Статистика"
@router.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cursor.fetchone())[0]
        
    text = (
        f"<b>👥 Всего пользователей:</b> <code>{total_users}</code>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilder()
            .button(text="◀️ Назад", callback_data="admin_back")
            .as_markup()
    )

@router.callback_query(lambda c: c.data == "admin_increment")
async def admin_increment_start(callback: CallbackQuery, state: FSMContext):
    # Редактируем сообщение, отправленное ботом, и сохраняем message_id
    sent_message = await callback.message.edit_text(
        "<b>Введите ID пользователя:</b>",
        reply_markup=cancel_keyboard()
    )
    await state.update_data(sent_message_id=sent_message.message_id)
    await state.set_state(AdminForm.user_id)

@router.message(AdminForm.user_id)
async def process_user_id(message: Message, state: FSMContext):
    data = await state.get_data()
    message_id = data.get("sent_message_id")
    try:
        await message.delete()
        user_id = int(message.text)
        if user_id not in users:
            raise ValueError
        await state.update_data(user_id=user_id)
        await state.set_state(AdminForm.amount)
        # Редактируем предыдущее бот-сообщение, чтобы заменить текст запроса
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_id,
            text="<b>Введите сумму для добавления:</b>",
            reply_markup=cancel_keyboard()
        )
    except Exception as e:
        await message.answer("❌ Неверный ID пользователя!")

@router.message(AdminForm.amount)
async def process_amount(message: Message, state: FSMContext):
    try:
        # Удаляем сообщение пользователя
        await message.delete()

        amount = float(message.text)
        data = await state.get_data()
        user = users[data['user_id']]
        
        # Здесь логика по добавлению сделки...
        deal_id = generate_deal_id()
        new_deal = Deal(deal_id, user.user_id, amount, "TON", "Админское изменение")
        new_deal.transfer_confirmed = True
        deals[deal_id] = new_deal
        await save_deal(new_deal)
        
        # Редактируем отправленное ботом сообщение, сохранив его message_id
        sent_message_id = data.get("sent_message_id")
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=sent_message_id,
            text=f"<b>✅ Добавлено</b> <code>{amount} TON</code> <b>к статистике пользователя</b> <code>{user.user_id}</code>",
            reply_markup=InlineKeyboardBuilder()
                .button(text="◀️ Назад", callback_data="admin_back")
                .as_markup()
        )
        await state.clear()
    except Exception:
        await message.answer("❌ Ошибка!")

# Обработчик рассылки
@router.callback_query(lambda c: c.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminForm.broadcast)
    await callback.message.edit_text(
        "<b>Введите сообщение для рассылки:</b>",
        reply_markup=cancel_keyboard()
    )

@router.message(AdminForm.broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    sent = 0
    failed = 0
    text = message.text
    for user_id in users.keys():
        try:
            await bot.send_message(user_id, text)
            sent += 1
            await asyncio.sleep(0.1)
        except TelegramBadRequest:
            failed += 1
    await message.answer(
        f"<b>✉️ Рассылка завершена!</b>\n"
        f"<i>✅ Успешно: {sent}</i>\n"
        f"<i>❌ Не удалось: {failed}</i>"
    )
    await state.clear()

# Кнопка "Назад" в админке
@router.callback_query(lambda c: c.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>⚙️ Админ-панель:</b>",
        reply_markup=admin_keyboard()
    )

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# Инициализация бота и диспетчера
support_bot = Bot(token=bot_support, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp2 = Dispatcher(storage=storage)

# Создаем роутеры
user_router = Router()
admin_router = Router()

# Словарь для связи сообщений
forwarded_messages = {}

# ================== ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЯ ================== #
@user_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "<b>👋 Привет! Отправь мне сообщение, "
        "и я перешлю его администратору.</b>"
    )

@user_router.message(F.chat.type == "private", ~F.text.startswith("/"))
async def user_message_handler(message: Message):
    # Форматирование сообщения для администратора
    msg_text = (
        f"📨 <b>Новое сообщение от пользователя</b>\n"
        f"👤 Имя: {message.from_user.full_name}\n"
        f"🆔 ID: {message.from_user.id}\n\n"
        f"📝 Текст:\n{message.text}"
    )
    
    # Отправка сообщения админу
    sent_msg = await bot.send_message(
        chat_id=suport_id,
        text=msg_text
    )
    
    # Сохраняем связь сообщений
    forwarded_messages[sent_msg.message_id] = message.from_user.id
    await message.answer("✅ Сообщение доставлено администратору!")

# ================== ОБРАБОТЧИКИ АДМИНИСТРАТОРА ================== #
@admin_router.message(F.reply_to_message)
async def admin_reply_handler(message: Message):
    # Проверяем что ответил администратор
    if message.from_user.id != suport_id:
        return
    
    # Ищем оригинальное сообщение
    original_message_id = message.reply_to_message.message_id
    user_id = forwarded_messages.get(original_message_id)
    
    if not user_id:
        await message.reply("❌ Ошибка: не найден пользователь для ответа!")
        return
    
    try:
        # Отправляем ответ пользователю
        await bot.send_message(
            chat_id=user_id,
            text=f"📩 <b>Ответ от администратора:</b>\n\n{message.text}"
        )
        await message.reply("✅ Ответ успешно отправлен!")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await message.reply("❌ Не удалось отправить ответ!")

dp = Dispatcher()

dp.include_router(router)

    
dp2.include_router(user_router)
dp2.include_router(admin_router)

async def start_main_bot():
  await dp.start_polling(bot)

async def start_support_bot():
  await dp2.start_polling(support_bot)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await init_db()  # Инициализация БД
    global users, deals, withdrawal_requests, referral_links
    users = await load_users()
    deals = await load_deals()
    withdrawal_requests = await load_withdrawal_requests()
    referral_links = await load_referral_links()
    
    await asyncio.gather(
      start_main_bot(),
      start_support_bot()
    )

if __name__ == '__main__':
    asyncio.run(main())
