import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import uuid
import re

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, CallbackQuery,
    Message, LabeledPrice, PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация бота
BOT_TOKEN = "8720303138:AAFZG7Wm68hXFVTVhcTbKX6lx8s6xf2Uiy0"
ADMIN_IDS = [5356400377]

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== КЛАССЫ ДАННЫХ ====================

class PaymentMethod(Enum):
    STARS = "stars"
    RUBLES = "rubles"

class OrderStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"

class ContestStatus(Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ENDED = "ended"

class Product:
    def __init__(self, id: str, name: str, description: str, price_rub: float, price_stars: float, stock: int = 999):
        self.id = id
        self.name = name
        self.description = description
        self.price_rub = float(price_rub)
        self.price_stars = float(price_stars)
        self.stock = stock
        self.created_at = datetime.now()

class Promotion:
    def __init__(self, code: str, discount_percent: int, valid_until: datetime, max_uses: int = 1):
        self.code = code.upper()
        self.discount_percent = discount_percent
        self.valid_until = valid_until
        self.max_uses = max_uses
        self.used_count = 0
        self.is_active = True

class Contest:
    def __init__(self, id: str, name: str, description: str, prize: str, 
                 required_purchase: bool = False, required_product_id: str = None,
                 start_date: datetime = None, end_date: datetime = None):
        self.id = id
        self.name = name
        self.description = description
        self.prize = prize
        self.required_purchase = required_purchase
        self.required_product_id = required_product_id
        self.start_date = start_date or datetime.now()
        self.end_date = end_date or (datetime.now() + timedelta(days=7))
        self.status = ContestStatus.ACTIVE
        self.participants = []  # list of user_ids
        self.winners = []
        self.created_at = datetime.now()
    
    def is_active(self) -> bool:
        return self.status == ContestStatus.ACTIVE and datetime.now() < self.end_date
    
    def add_participant(self, user_id: int):
        if user_id not in self.participants:
            self.participants.append(user_id)
    
    def has_participated(self, user_id: int) -> bool:
        return user_id in self.participants

class Order:
    def __init__(self, order_id: str, user_id: int, username: str, items: Dict[str, int], 
                 total_rub: float, total_stars: float, payment_method: PaymentMethod = None):
        self.order_id = order_id
        self.user_id = user_id
        self.username = username
        self.items = items
        self.total_rub = float(total_rub)
        self.total_stars = float(total_stars)
        self.payment_method = payment_method
        self.status = OrderStatus.PENDING
        self.created_at = datetime.now()
        self.confirmed_at = None
        self.completed_at = None
        self.completed_by = None
        self.screenshot_file_id = None
        self.promocode_used = None
        self.receipt_sent = False

class UserCart:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.items = {}
        self.promocode = None
        self.discount = 0

class SupportTicket:
    def __init__(self, ticket_id: str, user_id: int, username: str, message: str, message_id: int):
        self.ticket_id = ticket_id
        self.user_id = user_id
        self.username = username
        self.message = message
        self.message_id = message_id
        self.created_at = datetime.now()
        self.answered = False
        self.answer_message = None
        self.answered_at = None
        self.answered_by = None
        self.replies = []

    def add_reply(self, admin_id: int, reply_text: str):
        self.replies.append({
            'admin_id': admin_id,
            'text': reply_text,
            'created_at': datetime.now()
        })

class Review:
    def __init__(self, review_id: str, user_id: int, username: str, order_id: str, 
                 rating: int, comment: str, created_at: datetime = None):
        self.review_id = review_id
        self.user_id = user_id
        self.username = username
        self.order_id = order_id
        self.rating = rating
        self.comment = comment
        self.created_at = created_at or datetime.now()
        self.admin_reply = None
        self.admin_reply_at = None

# ==================== ХРАНИЛИЩЕ ДАННЫХ ====================

class Database:
    def __init__(self):
        self.products = {}
        self.orders = {}
        self.carts = {}
        self.promocodes = {}
        self.support_tickets = {}
        self.reviews = []
        self.contests = {}
        self.payment_details = {
            "card": "2200 0000 0000 0000",
            "phone": "+7 (999) 999-99-99",
            "bank": "Сбербанк"
        }
        self.next_product_id = 1
        self.next_ticket_id = 1
        self.next_review_id = 1
        self.next_contest_id = 1
        self.reviews_group_id = None
        self.bot_username = "ShopBot"

    def add_product(self, name: str, description: str, price_rub: float, price_stars: float, stock: int) -> Product:
        product_id = str(self.next_product_id)
        self.next_product_id += 1
        product = Product(product_id, name, description, price_rub, price_stars, stock)
        self.products[product_id] = product
        return product

    def update_product(self, product_id: str, **kwargs) -> Optional[Product]:
        if product_id in self.products:
            product = self.products[product_id]
            for key, value in kwargs.items():
                if hasattr(product, key):
                    setattr(product, key, value)
            return product
        return None

    def delete_product(self, product_id: str) -> bool:
        if product_id in self.products:
            del self.products[product_id]
            return True
        return False

    def get_product(self, product_id: str) -> Optional[Product]:
        return self.products.get(product_id)

    def get_all_products(self) -> List[Product]:
        return list(self.products.values())

    def get_cart(self, user_id: int) -> UserCart:
        if user_id not in self.carts:
            self.carts[user_id] = UserCart(user_id)
        return self.carts[user_id]

    def add_to_cart(self, user_id: int, product_id: str, quantity: int = 1) -> bool:
        product = self.get_product(product_id)
        if not product or product.stock < quantity:
            return False
        
        cart = self.get_cart(user_id)
        cart.items[product_id] = cart.items.get(product_id, 0) + quantity
        return True

    def remove_from_cart(self, user_id: int, product_id: str, quantity: int = None):
        cart = self.get_cart(user_id)
        if product_id in cart.items:
            if quantity is None or cart.items[product_id] <= quantity:
                del cart.items[product_id]
            else:
                cart.items[product_id] -= quantity

    def clear_cart(self, user_id: int):
        if user_id in self.carts:
            self.carts[user_id].items = {}
            self.carts[user_id].promocode = None
            self.carts[user_id].discount = 0

    def add_promocode(self, code: str, discount: int, days_valid: int = 7, max_uses: int = 1) -> Promotion:
        code = code.upper()
        valid_until = datetime.now() + timedelta(days=days_valid)
        promo = Promotion(code, discount, valid_until, max_uses)
        self.promocodes[code] = promo
        return promo

    def validate_promocode(self, code: str) -> Tuple[bool, int, str]:
        code = code.upper()
        if code not in self.promocodes:
            return False, 0, "Промокод не найден"
        
        promo = self.promocodes[code]
        if not promo.is_active:
            return False, 0, "Промокод неактивен"
        
        if datetime.now() > promo.valid_until:
            return False, 0, "Срок действия промокода истек"
        
        if promo.used_count >= promo.max_uses:
            return False, 0, "Промокод больше недействителен"
        
        return True, promo.discount_percent, f"Скидка {promo.discount_percent}%"

    def use_promocode(self, code: str) -> bool:
        code = code.upper()
        if code in self.promocodes:
            self.promocodes[code].used_count += 1
            return True
        return False

    def create_order(self, user_id: int, username: str, cart: UserCart, payment_method: PaymentMethod = None) -> Order:
        order_id = str(uuid.uuid4())[:8].upper()
        items = cart.items.copy()
        
        total_rub = 0.0
        total_stars = 0.0
        for product_id, quantity in items.items():
            product = self.get_product(product_id)
            if product:
                total_rub += product.price_rub * quantity
                total_stars += product.price_stars * quantity
        
        if cart.discount > 0:
            total_rub = total_rub * (100 - cart.discount) / 100
            total_stars = total_stars * (100 - cart.discount) / 100
        
        order = Order(order_id, user_id, username, items, total_rub, total_stars, payment_method)
        order.promocode_used = cart.promocode
        
        self.orders[order_id] = order
        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def get_user_orders(self, user_id: int) -> List[Order]:
        return [o for o in self.orders.values() if o.user_id == user_id]

    def get_pending_orders(self) -> List[Order]:
        return [o for o in self.orders.values() if o.status == OrderStatus.PENDING]

    def get_active_orders(self) -> List[Order]:
        return [o for o in self.orders.values() 
                if o.status in [OrderStatus.PAID, OrderStatus.CONFIRMED]]

    def get_completed_orders(self) -> List[Order]:
        return [o for o in self.orders.values() if o.status == OrderStatus.COMPLETED]

    def update_order_status(self, order_id: str, status: OrderStatus, completed_by: int = None) -> bool:
        if order_id in self.orders:
            order = self.orders[order_id]
            order.status = status
            
            if status in [OrderStatus.CONFIRMED, OrderStatus.COMPLETED]:
                order.confirmed_at = datetime.now()
            
            if status == OrderStatus.COMPLETED and completed_by:
                order.completed_at = datetime.now()
                order.completed_by = completed_by
            
            return True
        return False

    def set_order_screenshot(self, order_id: str, file_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id].screenshot_file_id = file_id
            return True
        return False

    def mark_receipt_sent(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id].receipt_sent = True
            return True
        return False

    def create_ticket(self, user_id: int, username: str, message: str, message_id: int) -> SupportTicket:
        ticket_id = f"T{self.next_ticket_id:04d}"
        self.next_ticket_id += 1
        ticket = SupportTicket(ticket_id, user_id, username, message, message_id)
        self.support_tickets[ticket_id] = ticket
        return ticket

    def get_ticket(self, ticket_id: str) -> Optional[SupportTicket]:
        return self.support_tickets.get(ticket_id)

    def get_all_tickets(self, answered: bool = None) -> List[SupportTicket]:
        tickets = list(self.support_tickets.values())
        if answered is not None:
            tickets = [t for t in tickets if t.answered == answered]
        return sorted(tickets, key=lambda x: x.created_at, reverse=True)

    def answer_ticket(self, ticket_id: str, admin_id: int, answer_text: str) -> bool:
        ticket = self.get_ticket(ticket_id)
        if ticket:
            ticket.answered = True
            ticket.answer_message = answer_text
            ticket.answered_at = datetime.now()
            ticket.answered_by = admin_id
            ticket.add_reply(admin_id, answer_text)
            return True
        return False

    def add_ticket_reply(self, ticket_id: str, admin_id: int, reply_text: str) -> bool:
        ticket = self.get_ticket(ticket_id)
        if ticket:
            ticket.add_reply(admin_id, reply_text)
            return True
        return False

    def add_review(self, user_id: int, username: str, order_id: str, rating: int, comment: str) -> Review:
        review_id = str(self.next_review_id)
        self.next_review_id += 1
        review = Review(review_id, user_id, username, order_id, rating, comment)
        self.reviews.append(review)
        return review

    def get_reviews(self, limit: int = 10) -> List[Review]:
        return sorted(self.reviews, key=lambda x: x.created_at, reverse=True)[:limit]

    def get_order_review(self, order_id: str) -> Optional[Review]:
        for review in self.reviews:
            if review.order_id == order_id:
                return review
        return None

    def add_admin_reply_to_review(self, review_id: str, reply_text: str) -> bool:
        for review in self.reviews:
            if review.review_id == review_id:
                review.admin_reply = reply_text
                review.admin_reply_at = datetime.now()
                return True
        return False

    def set_reviews_group(self, group_id: int):
        self.reviews_group_id = group_id

    def get_reviews_group(self) -> Optional[int]:
        return self.reviews_group_id

    def set_bot_username(self, username: str):
        self.bot_username = username

    def get_bot_username(self) -> str:
        return self.bot_username

    def update_payment_details(self, card: str = None, phone: str = None, bank: str = None):
        if card:
            self.payment_details["card"] = card
        if phone:
            self.payment_details["phone"] = phone
        if bank:
            self.payment_details["bank"] = bank

    def get_payment_details(self) -> dict:
        return self.payment_details.copy()

    # Методы для конкурсов
    def add_contest(self, name: str, description: str, prize: str, 
                    required_purchase: bool = False, required_product_id: str = None,
                    days_valid: int = 7) -> Contest:
        contest_id = str(self.next_contest_id)
        self.next_contest_id += 1
        end_date = datetime.now() + timedelta(days=days_valid)
        contest = Contest(contest_id, name, description, prize, 
                         required_purchase, required_product_id,
                         datetime.now(), end_date)
        self.contests[contest_id] = contest
        return contest

    def get_contest(self, contest_id: str) -> Optional[Contest]:
        return self.contests.get(contest_id)

    def get_active_contests(self) -> List[Contest]:
        return [c for c in self.contests.values() if c.is_active()]

    def get_all_contests(self) -> List[Contest]:
        return list(self.contests.values())

    def update_contest(self, contest_id: str, **kwargs) -> bool:
        if contest_id in self.contests:
            contest = self.contests[contest_id]
            for key, value in kwargs.items():
                if hasattr(contest, key):
                    setattr(contest, key, value)
            return True
        return False

    def delete_contest(self, contest_id: str) -> bool:
        if contest_id in self.contests:
            del self.contests[contest_id]
            return True
        return False

    def participate_in_contest(self, contest_id: str, user_id: int) -> bool:
        contest = self.get_contest(contest_id)
        if contest and contest.is_active():
            contest.add_participant(user_id)
            return True
        return False

    def has_participated_in_contest(self, contest_id: str, user_id: int) -> bool:
        contest = self.get_contest(contest_id)
        if contest:
            return contest.has_participated(user_id)
        return False

    def check_purchase_requirement(self, contest_id: str, user_id: int) -> bool:
        contest = self.get_contest(contest_id)
        if not contest or not contest.required_purchase:
            return True
        
        if not contest.required_product_id:
            return True
        
        # Проверяем, покупал ли пользователь нужный товар
        user_orders = self.get_user_orders(user_id)
        for order in user_orders:
            if order.status == OrderStatus.COMPLETED:
                for product_id in order.items:
                    if product_id == contest.required_product_id:
                        return True
        return False

db = Database()

# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard(is_admin: bool = False):
    buttons = [
        [KeyboardButton(text="🛍 Каталог")],
        [KeyboardButton(text="🛒 Корзина"), KeyboardButton(text="📦 Мои заказы")],
        [KeyboardButton(text="ℹ️ Поддержка"), KeyboardButton(text="📝 Промокод")],
        [KeyboardButton(text="⭐ Отзывы"), KeyboardButton(text="🧾 Чеки")],
        [KeyboardButton(text="🎁 Конкурсы")]
    ]
    
    if is_admin:
        buttons.append([KeyboardButton(text="⚙️ Админ панель")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_keyboard():
    buttons = [
        [KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="✏️ Редактировать товар"), KeyboardButton(text="❌ Удалить товар")],
        [KeyboardButton(text="📦 Все заказы"), KeyboardButton(text="⏳ Ожидают оплаты")],
        [KeyboardButton(text="🔄 Активные заказы"), KeyboardButton(text="✅ Завершенные заказы")],
        [KeyboardButton(text="🎫 Создать промокод"), KeyboardButton(text="💳 Реквизиты оплаты")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📨 Ответы поддержки")],
        [KeyboardButton(text="⭐ Управление отзывами"), KeyboardButton(text="🎁 Управление конкурсами")],
        [KeyboardButton(text="👤 Установить имя"), KeyboardButton(text="🔙 На главную")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

def get_reviews_keyboard():
    buttons = [
        [KeyboardButton(text="📝 Написать отзыв")],
        [KeyboardButton(text="📖 Все отзывы")],
        [KeyboardButton(text="🔙 На главную")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_my_orders_keyboard():
    buttons = [
        [KeyboardButton(text="🔄 Активные заказы")],
        [KeyboardButton(text="✅ Завершенные заказы")],
        [KeyboardButton(text="🔙 На главную")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_products_inline_keyboard(page: int = 0, items_per_page: int = 5):
    products = db.get_all_products()
    total_pages = (len(products) + items_per_page - 1) // items_per_page
    
    start = page * items_per_page
    end = start + items_per_page
    page_products = products[start:end]
    
    builder = InlineKeyboardBuilder()
    
    for product in page_products:
        stock_status = f" (в наличии: {product.stock})" if product.stock > 0 else " (нет в наличии)"
        rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
        stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
        
        builder.row(InlineKeyboardButton(
            text=f"{product.name} - {rub_price}₽ / {stars_price}⭐{stock_status}",
            callback_data=f"product_{product.id}"
        ))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"page_{page+1}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(InlineKeyboardButton(text="🛒 Перейти в корзину", callback_data="view_cart"))
    
    return builder.as_markup()

def get_product_inline_keyboard(product_id: str, current_qty: int = 1):
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="➖", callback_data=f"dec_{product_id}"),
        InlineKeyboardButton(text=f"{current_qty}", callback_data="show_qty"),
        InlineKeyboardButton(text="➕", callback_data=f"inc_{product_id}")
    )
    
    builder.row(
        InlineKeyboardButton(text="✏️ Ввести количество вручную", callback_data=f"manual_qty_{product_id}")
    )
    
    builder.row(
        InlineKeyboardButton(text="✅ Добавить в корзину", callback_data=f"add_{product_id}")
    )
    
    builder.row(
        InlineKeyboardButton(text="◀️ Назад к каталогу", callback_data="back_to_catalog")
    )
    
    return builder.as_markup()

def get_cart_inline_keyboard(user_id: int):
    cart = db.get_cart(user_id)
    builder = InlineKeyboardBuilder()
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            builder.row(InlineKeyboardButton(
                text=f"❌ {product.name} x{quantity}",
                callback_data=f"remove_{product_id}"
            ))
    
    if cart.items:
        builder.row(
            InlineKeyboardButton(text="⭐ Оплатить звездами", callback_data="pay_stars"),
            InlineKeyboardButton(text="💳 Оплатить рублями", callback_data="pay_rubles")
        )
        builder.row(InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart"))
    
    builder.row(InlineKeyboardButton(text="◀️ Продолжить покупки", callback_data="back_to_catalog"))
    
    return builder.as_markup()

def get_order_actions_inline_keyboard(order_id: str, is_admin: bool = False):
    builder = InlineKeyboardBuilder()
    
    if is_admin:
        order = db.get_order(order_id)
        builder.row(
            InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"admin_confirm_{order_id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel_{order_id}")
        )
        
        if order and order.status in [OrderStatus.PAID, OrderStatus.CONFIRMED]:
            builder.row(InlineKeyboardButton(
                text="🎉 Завершить заказ", 
                callback_data=f"admin_complete_{order_id}"
            ))
        
        if order and order.screenshot_file_id:
            builder.row(InlineKeyboardButton(
                text="📸 Просмотреть скрин", 
                callback_data=f"view_screen_{order_id}"
            ))
    else:
        order = db.get_order(order_id)
        if order and order.status == OrderStatus.PENDING and order.payment_method == PaymentMethod.RUBLES:
            builder.row(InlineKeyboardButton(
                text="📸 Отправить скрин оплаты", 
                callback_data=f"send_screen_{order_id}"
            ))
        elif order and order.status in [OrderStatus.PAID, OrderStatus.CONFIRMED]:
            builder.row(InlineKeyboardButton(
                text="🔄 Обновить статус", 
                callback_data=f"refresh_order_{order_id}"
            ))
    
    return builder.as_markup()

def get_receipts_inline_keyboard(user_id: int):
    orders = db.get_user_orders(user_id)
    completed_orders = [o for o in orders if o.status == OrderStatus.COMPLETED]
    
    builder = InlineKeyboardBuilder()
    
    for order in completed_orders[:5]:
        builder.row(InlineKeyboardButton(
            text=f"🧾 Заказ #{order.order_id} - {order.created_at.strftime('%d.%m.%Y')}",
            callback_data=f"receipt_{order.order_id}"
        ))
    
    if not completed_orders:
        builder.row(InlineKeyboardButton(text="📭 Нет завершенных заказов", callback_data="noop"))
    
    builder.row(InlineKeyboardButton(text="◀️ На главную", callback_data="back_to_main"))
    
    return builder.as_markup()

def get_reviews_inline_keyboard(page: int = 0):
    reviews = db.get_reviews(limit=20)
    items_per_page = 3
    total_pages = (len(reviews) + items_per_page - 1) // items_per_page
    
    start = page * items_per_page
    end = start + items_per_page
    page_reviews = reviews[start:end]
    
    builder = InlineKeyboardBuilder()
    
    for review in page_reviews:
        stars = "⭐" * review.rating
        builder.row(InlineKeyboardButton(
            text=f"{stars} - @{review.username} - {review.created_at.strftime('%d.%m.%Y')}",
            callback_data=f"view_review_{review.review_id}"
        ))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"reviews_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"reviews_page_{page+1}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(InlineKeyboardButton(text="📝 Написать отзыв", callback_data="write_review"))
    builder.row(InlineKeyboardButton(text="◀️ На главную", callback_data="back_to_main"))
    
    return builder.as_markup()

def get_support_tickets_inline_keyboard(page: int = 0, answered: bool = False):
    tickets = db.get_all_tickets(answered=answered)
    items_per_page = 5
    total_pages = (len(tickets) + items_per_page - 1) // items_per_page
    
    start = page * items_per_page
    end = start + items_per_page
    page_tickets = tickets[start:end]
    
    builder = InlineKeyboardBuilder()
    
    for ticket in page_tickets:
        status = "✅" if ticket.answered else "⏳"
        builder.row(InlineKeyboardButton(
            text=f"{status} #{ticket.ticket_id} - @{ticket.username}",
            callback_data=f"view_ticket_{ticket.ticket_id}"
        ))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"tickets_page_{page-1}_{answered}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"tickets_page_{page+1}_{answered}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(
        InlineKeyboardButton(text="⏳ Ожидают", callback_data="tickets_pending"),
        InlineKeyboardButton(text="✅ Отвеченные", callback_data="tickets_answered")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    return builder.as_markup()

def get_manual_quantity_inline_keyboard(product_id: str):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_manual_qty_{product_id}"))
    return builder.as_markup()

def get_contests_inline_keyboard():
    contests = db.get_active_contests()
    
    builder = InlineKeyboardBuilder()
    
    for contest in contests:
        builder.row(InlineKeyboardButton(
            text=f"🎁 {contest.name}",
            callback_data=f"contest_{contest.id}"
        ))
    
    builder.row(InlineKeyboardButton(text="◀️ На главную", callback_data="back_to_main"))
    
    return builder.as_markup()

def get_admin_contests_inline_keyboard():
    contests = db.get_all_contests()
    
    builder = InlineKeyboardBuilder()
    
    for contest in contests:
        status = "🟢" if contest.is_active() else "🔴"
        builder.row(InlineKeyboardButton(
            text=f"{status} {contest.name}",
            callback_data=f"admin_contest_{contest.id}"
        ))
    
    builder.row(InlineKeyboardButton(text="➕ Создать конкурс", callback_data="create_contest"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    return builder.as_markup()

# ==================== СОСТОЯНИЯ FSM ====================

class AdminStates(StatesGroup):
    waiting_for_product_name = State()
    waiting_for_product_description = State()
    waiting_for_product_price_rub = State()
    waiting_for_product_price_stars = State()
    waiting_for_product_stock = State()
    waiting_for_product_id_to_edit = State()
    waiting_for_product_edit_field = State()
    waiting_for_product_edit_value = State()
    waiting_for_product_id_to_delete = State()
    waiting_for_promocode_code = State()
    waiting_for_promocode_discount = State()
    waiting_for_promocode_days = State()
    waiting_for_promocode_uses = State()
    waiting_for_payment_card = State()
    waiting_for_payment_phone = State()
    waiting_for_payment_bank = State()
    waiting_for_ticket_answer = State()
    waiting_for_ticket_reply = State()
    waiting_for_review_reply = State()
    waiting_for_group_setup = State()
    waiting_for_bot_username = State()
    waiting_for_contest_name = State()
    waiting_for_contest_description = State()
    waiting_for_contest_prize = State()
    waiting_for_contest_days = State()
    waiting_for_contest_required_purchase = State()
    waiting_for_contest_required_product = State()

class UserStates(StatesGroup):
    waiting_for_promocode = State()
    waiting_for_support_message = State()
    waiting_for_screenshot = State()
    waiting_for_review_rating = State()
    waiting_for_review_comment = State()
    waiting_for_order_selection = State()
    waiting_for_manual_quantity = State()

# ==================== ФИЛЬТРЫ ====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_group_chat(message: Message) -> bool:
    return message.chat.type in ['group', 'supergroup']

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    
    await message.answer(
        f"👋 Добро пожаловать в магазин!\n\n"
        f"Здесь вы можете приобрести товары за звезды или рубли.\n"
        f"Используйте кнопки ниже для навигации.",
        reply_markup=get_main_keyboard(is_admin(user_id))
    )

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if is_group_chat(message):
        return
    
    if is_admin(message.from_user.id):
        await message.answer(
            "⚙️ Админ панель",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer("❌ У вас нет прав администратора.")

@dp.message(Command("setreviewsgroup"))
async def set_reviews_group(message: Message):
    if not is_group_chat(message):
        await message.answer("❌ Эта команда должна быть отправлена в группе!")
        return
    
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    try:
        chat_member = await bot.get_chat_member(message.chat.id, bot.id)
        if chat_member.status not in ['administrator', 'creator']:
            await message.answer(
                "❌ Бот должен быть администратором группы для отправки отзывов!\n"
                "Сделайте бота администратором и попробуйте снова."
            )
            return
    except:
        await message.answer("❌ Не удалось проверить права бота. Убедитесь, что бот добавлен в группу.")
        return
    
    db.set_reviews_group(message.chat.id)
    
    await message.answer(
        f"✅ Группа настроена для получения отзывов!\n"
        f"ID группы: `{message.chat.id}`\n\n"
        f"Теперь все новые отзывы будут автоматически отправляться сюда."
    )

# ==================== ОБЩИЕ ОБРАБОТЧИКИ ====================

@dp.message(F.text == "🛍 Каталог")
async def show_catalog(message: Message):
    if is_group_chat(message):
        return
    
    products = db.get_all_products()
    
    if not products:
        await message.answer("📭 Каталог пуст. Товары появятся позже.")
        return
    
    await message.answer(
        "🛍 Наш каталог:",
        reply_markup=get_products_inline_keyboard()
    )

@dp.message(F.text == "🛒 Корзина")
async def show_cart(message: Message):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    cart = db.get_cart(user_id)
    
    if not cart.items:
        await message.answer("🛒 Ваша корзина пуста.")
        return
    
    cart_text = "🛒 *Ваша корзина:*\n\n"
    total_rub = 0.0
    total_stars = 0.0
    items_details = []
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            item_rub = product.price_rub * quantity
            item_stars = product.price_stars * quantity
            items_details.append({
                'name': product.name,
                'quantity': quantity,
                'price_rub': product.price_rub,
                'price_stars': product.price_stars,
                'total_rub': item_rub,
                'total_stars': item_stars
            })
            total_rub += item_rub
            total_stars += item_stars
    
    original_rub = total_rub
    original_stars = total_stars
    
    if cart.discount > 0:
        total_rub = total_rub * (100 - cart.discount) / 100
        total_stars = total_stars * (100 - cart.discount) / 100
    
    for item in items_details:
        rub_price = f"{item['price_rub']:.2f}".rstrip('0').rstrip('.') if item['price_rub'] % 1 else str(int(item['price_rub']))
        stars_price = f"{item['price_stars']:.2f}".rstrip('0').rstrip('.') if item['price_stars'] % 1 else str(int(item['price_stars']))
        item_total_rub = f"{item['total_rub']:.2f}".rstrip('0').rstrip('.') if item['total_rub'] % 1 else str(int(item['total_rub']))
        item_total_stars = f"{item['total_stars']:.2f}".rstrip('0').rstrip('.') if item['total_stars'] % 1 else str(int(item['total_stars']))
        
        cart_text += f"• *{item['name']}* x{item['quantity']}\n"
        cart_text += f"  {rub_price}₽ x{item['quantity']} = {item_total_rub}₽\n"
        cart_text += f"  {stars_price}⭐ x{item['quantity']} = {item_total_stars}⭐\n\n"
    
    if cart.discount > 0:
        original_rub_str = f"{original_rub:.2f}".rstrip('0').rstrip('.') if original_rub % 1 else str(int(original_rub))
        original_stars_str = f"{original_stars:.2f}".rstrip('0').rstrip('.') if original_stars % 1 else str(int(original_stars))
        total_rub_str = f"{total_rub:.2f}".rstrip('0').rstrip('.') if total_rub % 1 else str(int(total_rub))
        total_stars_str = f"{total_stars:.2f}".rstrip('0').rstrip('.') if total_stars % 1 else str(int(total_stars))
        
        cart_text += f"*Скидка по промокоду* `{cart.promocode}`: {cart.discount}%\n"
        cart_text += f"*Было:* {original_rub_str}₽ / {original_stars_str}⭐\n"
        cart_text += f"*Стало:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    else:
        total_rub_str = f"{total_rub:.2f}".rstrip('0').rstrip('.') if total_rub % 1 else str(int(total_rub))
        total_stars_str = f"{total_stars:.2f}".rstrip('0').rstrip('.') if total_stars % 1 else str(int(total_stars))
        cart_text += f"*Итого:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    
    await message.answer(
        cart_text,
        parse_mode="Markdown",
        reply_markup=get_cart_inline_keyboard(user_id)
    )

@dp.message(F.text == "📦 Мои заказы")
async def show_my_orders_menu(message: Message):
    if is_group_chat(message):
        return
    
    await message.answer(
        "📦 *Ваши заказы*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=get_my_orders_keyboard()
    )

@dp.message(F.text == "🔄 Активные заказы")
async def show_active_orders(message: Message):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    orders = db.get_user_orders(user_id)
    active_orders = [o for o in orders if o.status in [OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.CONFIRMED]]
    
    if not active_orders:
        await message.answer("📭 У вас нет активных заказов.")
        return
    
    active_orders.sort(key=lambda x: x.created_at, reverse=True)
    
    for order in active_orders:
        status_emoji = {
            OrderStatus.PENDING: "⏳",
            OrderStatus.PAID: "✅",
            OrderStatus.CONFIRMED: "👍",
        }.get(order.status, "❓")
        
        status_text = {
            OrderStatus.PENDING: "Ожидает оплаты",
            OrderStatus.PAID: "Оплачено, ожидает выполнения",
            OrderStatus.CONFIRMED: "Подтверждено, выполняется",
        }.get(order.status, order.status.value)
        
        payment_method = "⭐ Звезды" if order.payment_method == PaymentMethod.STARS else "💳 Рубли"
        
        total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        text = (
            f"{status_emoji} *Заказ #{order.order_id}*\n"
            f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"💳 Оплата: {payment_method}\n"
            f"💰 Сумма: {total_rub_str}₽ / {total_stars_str}⭐\n"
            f"📊 Статус: {status_text}\n"
        )
        
        if order.promocode_used:
            text += f"🏷 Промокод: {order.promocode_used}\n"
        
        if order.status == OrderStatus.PAID or order.status == OrderStatus.CONFIRMED:
            text += f"\n🔄 Заказ в обработке. Вы можете обновить статус."
        
        keyboard = get_order_actions_inline_keyboard(order.order_id)
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.message(F.text == "✅ Завершенные заказы")
async def show_completed_orders(message: Message):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    orders = db.get_user_orders(user_id)
    completed_orders = [o for o in orders if o.status == OrderStatus.COMPLETED]
    
    if not completed_orders:
        await message.answer("📭 У вас пока нет завершенных заказов.")
        return
    
    completed_orders.sort(key=lambda x: x.created_at, reverse=True)
    
    for order in completed_orders[:5]:
        payment_method = "⭐ Звезды" if order.payment_method == PaymentMethod.STARS else "💳 Рубли"
        
        total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        text = (
            f"🎉 *Заказ #{order.order_id}*\n"
            f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"✅ Завершен: {order.completed_at.strftime('%d.%m.%Y %H:%M') if order.completed_at else 'Неизвестно'}\n"
            f"💳 Оплата: {payment_method}\n"
            f"💰 Сумма: {total_rub_str}₽ / {total_stars_str}⭐\n"
        )
        
        if order.promocode_used:
            text += f"🏷 Промокод: {order.promocode_used}\n"
        
        existing_review = db.get_order_review(order.order_id)
        if not existing_review:
            text += f"\n📝 Вы можете оставить отзыв о заказе."
            keyboard = InlineKeyboardBuilder()
            keyboard.row(InlineKeyboardButton(
                text="📝 Написать отзыв",
                callback_data=f"review_order_{order.order_id}"
            ))
            await message.answer(text, parse_mode="Markdown", reply_markup=keyboard.as_markup())
        else:
            await message.answer(text, parse_mode="Markdown")
    
    if len(completed_orders) > 5:
        await message.answer(f"Показано 5 из {len(completed_orders)} завершенных заказов")

@dp.callback_query(F.data.startswith("refresh_order_"))
async def refresh_order_status(callback: CallbackQuery):
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    status_emoji = {
        OrderStatus.PENDING: "⏳",
        OrderStatus.PAID: "✅",
        OrderStatus.CONFIRMED: "👍",
        OrderStatus.COMPLETED: "🎉",
        OrderStatus.CANCELLED: "❌"
    }.get(order.status, "❓")
    
    status_text = {
        OrderStatus.PENDING: "Ожидает оплаты",
        OrderStatus.PAID: "Оплачено, ожидает выполнения",
        OrderStatus.CONFIRMED: "Подтверждено, выполняется",
        OrderStatus.COMPLETED: "Завершен",
        OrderStatus.CANCELLED: "Отменен"
    }.get(order.status, order.status.value)
    
    await callback.answer(f"Статус заказа: {status_text}", show_alert=False)
    
    try:
        await callback.message.edit_text(
            callback.message.text,
            parse_mode="Markdown",
            reply_markup=get_order_actions_inline_keyboard(order_id)
        )
    except:
        pass

@dp.message(F.text == "ℹ️ Поддержка")
async def support_request(message: Message, state: FSMContext):
    if is_group_chat(message):
        return
    
    await state.set_state(UserStates.waiting_for_support_message)
    await message.answer(
        "📝 Опишите вашу проблему или вопрос. Напишите сообщение, и мы ответим вам в ближайшее время.\n\n"
        "Вы также можете прикрепить фото или документ, если это необходимо.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(UserStates.waiting_for_support_message)
async def process_support_message(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_main_keyboard(is_admin(message.from_user.id))
        )
        return
    
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    
    ticket = db.create_ticket(user_id, username, message.text or message.caption or "[Медиасообщение]", message.message_id)
    
    for admin_id in ADMIN_IDS:
        try:
            await message.copy_to(
                admin_id,
                caption=(
                    f"📨 *Новое обращение в поддержку*\n"
                    f"Тикет: #{ticket.ticket_id}\n"
                    f"От: @{username} (ID: {user_id})\n"
                    f"Сообщение: {message.text or message.caption or 'Медиасообщение'}"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"answer_ticket_{ticket.ticket_id}")]
                    ]
                )
            )
        except Exception as e:
            logger.error(f"Failed to forward to admin {admin_id}: {e}")
    
    await message.answer(
        "✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время.\n"
        f"Номер вашего обращения: #{ticket.ticket_id}",
        reply_markup=get_main_keyboard(is_admin(user_id))
    )
    await state.clear()

@dp.message(F.text == "📝 Промокод")
async def enter_promocode(message: Message, state: FSMContext):
    if is_group_chat(message):
        return
    
    await state.set_state(UserStates.waiting_for_promocode)
    await message.answer(
        "Введите промокод:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(UserStates.waiting_for_promocode)
async def process_promocode(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_main_keyboard(is_admin(message.from_user.id))
        )
        return
    
    code = message.text.strip().upper()
    valid, discount, msg = db.validate_promocode(code)
    
    if valid:
        cart = db.get_cart(message.from_user.id)
        cart.promocode = code
        cart.discount = discount
        db.use_promocode(code)
        
        response = f"✅ Промокод применен! Скидка {discount}%\n\n"
        
        await message.answer(
            response,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(is_admin(message.from_user.id))
        )
    else:
        await message.answer(
            f"❌ {msg}",
            reply_markup=get_main_keyboard(is_admin(message.from_user.id))
        )
    
    await state.clear()

@dp.message(F.text == "🧾 Чеки")
async def show_receipts_menu(message: Message):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    orders = db.get_user_orders(user_id)
    completed_orders = [o for o in orders if o.status == OrderStatus.COMPLETED]
    
    if not completed_orders:
        await message.answer("🧾 У вас пока нет завершенных заказов для получения чеков.")
        return
    
    await message.answer(
        "🧾 *Ваши чеки*\n\nВыберите заказ для просмотра чека:",
        parse_mode="Markdown",
        reply_markup=get_receipts_inline_keyboard(user_id)
    )

@dp.callback_query(F.data.startswith("receipt_"))
async def show_receipt(callback: CallbackQuery):
    order_id = callback.data.split("_")[1]
    order = db.get_order(order_id)
    
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    receipt_text = f"🧾 *ЧЕК #{order.order_id}*\n"
    receipt_text += "=" * 30 + "\n\n"
    
    receipt_text += f"📅 *Дата:* {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    receipt_text += f"👤 *Покупатель:* @{order.username}\n"
    receipt_text += f"👨‍💼 *Продавец:* {db.get_bot_username()}\n\n"
    
    receipt_text += "*Товары:*\n"
    for product_id, quantity in order.items.items():
        product = db.get_product(product_id)
        if product:
            rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.')
            stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.')
            receipt_text += f"• {product.name} x{quantity}\n"
            receipt_text += f"  {rub_price}₽ x{quantity} = {product.price_rub * quantity:.2f}₽\n"
            receipt_text += f"  {stars_price}⭐ x{quantity} = {product.price_stars * quantity:.2f}⭐\n"
    
    receipt_text += "\n" + "=" * 30 + "\n"
    
    if order.promocode_used:
        receipt_text += f"🏷 *Промокод:* {order.promocode_used}\n"
    
    total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.')
    total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.')
    
    receipt_text += f"💳 *Способ оплаты:* {'⭐ Звезды' if order.payment_method == PaymentMethod.STARS else '💳 Рубли'}\n"
    receipt_text += f"💰 *ИТОГО:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    
    if order.completed_at:
        receipt_text += f"🎉 *Завершен:* {order.completed_at.strftime('%d.%m.%Y %H:%M')}\n"
    
    receipt_text += "\n" + "=" * 30 + "\n"
    receipt_text += "Спасибо за покупку!"
    
    db.mark_receipt_sent(order_id)
    
    await callback.message.answer(receipt_text, parse_mode="Markdown")
    
    if order.screenshot_file_id and order.payment_method == PaymentMethod.RUBLES:
        await callback.message.answer_photo(
            order.screenshot_file_id,
            caption="📸 Ваш скриншот оплаты (копия)"
        )
    
    await callback.answer()

# ==================== КОНКУРСЫ ====================

@dp.message(F.text == "🎁 Конкурсы")
async def show_contests(message: Message):
    if is_group_chat(message):
        return
    
    active_contests = db.get_active_contests()
    
    if not active_contests:
        await message.answer(
            "🎁 На данный момент нет активных конкурсов.\n"
            "Следите за обновлениями!"
        )
        return
    
    await message.answer(
        "🎁 *Активные конкурсы*\n\n"
        "Выберите конкурс для участия:",
        parse_mode="Markdown",
        reply_markup=get_contests_inline_keyboard()
    )

@dp.callback_query(F.data.startswith("contest_"))
async def view_contest(callback: CallbackQuery):
    contest_id = callback.data.split("_")[1]
    contest = db.get_contest(contest_id)
    
    if not contest or not contest.is_active():
        await callback.answer("Конкурс не найден или уже завершен", show_alert=True)
        return
    
    # Проверяем требование покупки
    if contest.required_purchase:
        has_purchased = db.check_purchase_requirement(contest_id, callback.from_user.id)
        if not has_purchased:
            product = db.get_product(contest.required_product_id) if contest.required_product_id else None
            product_text = f"товар \"{product.name}\"" if product else "необходимый товар"
            
            await callback.message.answer(
                f"❌ Для участия в этом конкурсе необходимо приобрести {product_text}.\n\n"
                f"После покупки вы сможете участвовать в конкурсе автоматически.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🛍 Перейти в каталог", callback_data="back_to_catalog")],
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_contests")]
                    ]
                )
            )
            await callback.answer()
            return
    
    # Проверяем, участвовал ли уже пользователь
    has_participated = db.has_participated_in_contest(contest_id, callback.from_user.id)
    
    end_date = contest.end_date.strftime('%d.%m.%Y %H:%M')
    remaining = contest.end_date - datetime.now()
    days = remaining.days
    hours = remaining.seconds // 3600
    
    contest_text = (
        f"🎁 *{contest.name}*\n\n"
        f"{contest.description}\n\n"
        f"🏆 *Приз:* {contest.prize}\n"
        f"📅 *Окончание:* {end_date}\n"
        f"⏰ *Осталось:* {days} д. {hours} ч.\n"
        f"👥 *Участников:* {len(contest.participants)}\n"
    )
    
    if contest.required_purchase:
        product = db.get_product(contest.required_product_id) if contest.required_product_id else None
        if product:
            contest_text += f"📦 *Требуется покупка:* {product.name}\n"
    
    keyboard = InlineKeyboardBuilder()
    
    if has_participated:
        keyboard.row(InlineKeyboardButton(text="✅ Вы уже участвуете", callback_data="noop"))
    else:
        keyboard.row(InlineKeyboardButton(text="🎲 Участвовать", callback_data=f"participate_{contest_id}"))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад к конкурсам", callback_data="back_to_contests"))
    
    await callback.message.edit_text(
        contest_text,
        parse_mode="Markdown",
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("participate_"))
async def participate_in_contest(callback: CallbackQuery):
    contest_id = callback.data.split("_")[1]
    contest = db.get_contest(contest_id)
    
    if not contest or not contest.is_active():
        await callback.answer("Конкурс не найден или уже завершен", show_alert=True)
        return
    
    # Проверяем требование покупки
    if contest.required_purchase:
        has_purchased = db.check_purchase_requirement(contest_id, callback.from_user.id)
        if not has_purchased:
            product = db.get_product(contest.required_product_id) if contest.required_product_id else None
            product_text = f"товар \"{product.name}\"" if product else "необходимый товар"
            
            await callback.answer(f"Для участия необходимо приобрести {product_text}", show_alert=True)
            return
    
    if db.has_participated_in_contest(contest_id, callback.from_user.id):
        await callback.answer("Вы уже участвуете в этом конкурсе!", show_alert=True)
        return
    
    db.participate_in_contest(contest_id, callback.from_user.id)
    
    await callback.answer("✅ Вы успешно участвуете в конкурсе! Удачи!", show_alert=True)
    
    # Обновляем сообщение
    await view_contest(callback)

@dp.callback_query(F.data == "back_to_contests")
async def back_to_contests(callback: CallbackQuery):
    active_contests = db.get_active_contests()
    
    if not active_contests:
        await callback.message.edit_text(
            "🎁 На данный момент нет активных конкурсов.\n"
            "Следите за обновлениями!"
        )
    else:
        await callback.message.edit_text(
            "🎁 *Активные конкурсы*\n\n"
            "Выберите конкурс для участия:",
            parse_mode="Markdown",
            reply_markup=get_contests_inline_keyboard()
        )
    await callback.answer()

# ==================== АДМИН-ПАНЕЛЬ КОНКУРСОВ ====================

@dp.message(F.text == "🎁 Управление конкурсами")
async def manage_contests(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await message.answer(
        "🎁 *Управление конкурсами*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_contests_inline_keyboard()
    )

@dp.callback_query(F.data == "create_contest")
async def create_contest_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return
    
    await state.set_state(AdminStates.waiting_for_contest_name)
    await message.answer(
        "Введите название конкурса:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_contest_name)
async def create_contest_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(contest_name=message.text)
    await state.set_state(AdminStates.waiting_for_contest_description)
    await message.answer(
        "Введите описание конкурса:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_contest_description)
async def create_contest_description(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(contest_description=message.text)
    await state.set_state(AdminStates.waiting_for_contest_prize)
    await message.answer(
        "Введите приз конкурса:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_contest_prize)
async def create_contest_prize(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(contest_prize=message.text)
    await state.set_state(AdminStates.waiting_for_contest_days)
    await message.answer(
        "Введите количество дней действия конкурса:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_contest_days)
async def create_contest_days(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        days = int(message.text)
        if days < 1:
            raise ValueError
        
        await state.update_data(contest_days=days)
        await state.set_state(AdminStates.waiting_for_contest_required_purchase)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да", callback_data="req_purchase_yes")],
                [InlineKeyboardButton(text="❌ Нет", callback_data="req_purchase_no")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
        
        await message.answer(
            "Требуется ли обязательная покупка для участия?",
            reply_markup=keyboard
        )
    except ValueError:
        await message.answer("❌ Введите корректное число дней")

@dp.callback_query(F.data.startswith("req_purchase_"))
async def create_contest_required_purchase(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return
    
    required = callback.data == "req_purchase_yes"
    await state.update_data(contest_required_purchase=required)
    
    if required:
        await state.set_state(AdminStates.waiting_for_contest_required_product)
        
        products = db.get_all_products()
        if not products:
            await callback.message.answer(
                "❌ Нет доступных товаров. Сначала добавьте товары.",
                reply_markup=get_admin_keyboard()
            )
            await state.clear()
            await callback.answer()
            return
        
        builder = InlineKeyboardBuilder()
        for product in products:
            builder.row(InlineKeyboardButton(
                text=f"{product.name} - {product.price_rub}₽",
                callback_data=f"req_product_{product.id}"
            ))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
        
        await callback.message.edit_text(
            "Выберите товар, который нужно купить для участия:",
            reply_markup=builder.as_markup()
        )
    else:
        contest = db.add_contest(
            (await state.get_data())['contest_name'],
            (await state.get_data())['contest_description'],
            (await state.get_data())['contest_prize'],
            False,
            None,
            (await state.get_data())['contest_days']
        )
        
        await callback.message.edit_text(
            f"✅ Конкурс \"{contest.name}\" успешно создан!\n\n"
            f"📅 Длительность: {contest.end_date.strftime('%d.%m.%Y')}\n"
            f"🏆 Приз: {contest.prize}\n"
            f"🎲 Участники смогут участвовать сразу."
        )
        await state.clear()
    
    await callback.answer()

@dp.callback_query(F.data.startswith("req_product_"))
async def create_contest_required_product(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return
    
    product_id = callback.data.split("_")[2]
    data = await state.get_data()
    
    contest = db.add_contest(
        data['contest_name'],
        data['contest_description'],
        data['contest_prize'],
        True,
        product_id,
        data['contest_days']
    )
    
    product = db.get_product(product_id)
    
    await callback.message.edit_text(
        f"✅ Конкурс \"{contest.name}\" успешно создан!\n\n"
        f"📅 Длительность: {contest.end_date.strftime('%d.%m.%Y')}\n"
        f"🏆 Приз: {contest.prize}\n"
        f"📦 Требуется покупка: {product.name if product else 'Неизвестный товар'}\n\n"
        f"Пользователи смогут участвовать только после покупки этого товара."
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_contest_"))
async def admin_view_contest(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if not contest:
        await callback.answer("Конкурс не найден", show_alert=True)
        return
    
    status_text = "🟢 Активен" if contest.is_active() else "🔴 Завершен"
    
    text = (
        f"🎁 *{contest.name}*\n\n"
        f"📝 {contest.description}\n\n"
        f"🏆 *Приз:* {contest.prize}\n"
        f"📊 *Статус:* {status_text}\n"
        f"📅 *Создан:* {contest.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📅 *Окончание:* {contest.end_date.strftime('%d.%m.%Y %H:%M')}\n"
        f"👥 *Участников:* {len(contest.participants)}\n"
    )
    
    if contest.required_purchase:
        product = db.get_product(contest.required_product_id) if contest.required_product_id else None
        text += f"📦 *Требуется покупка:* {product.name if product else 'Неизвестный товар'}\n"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Завершить конкурс", callback_data=f"end_contest_{contest_id}")] if contest.is_active() else [],
            [InlineKeyboardButton(text="🗑 Удалить конкурс", callback_data=f"delete_contest_{contest_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_contests_admin")]
        ]
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("end_contest_"))
async def end_contest(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if contest:
        contest.status = ContestStatus.ENDED
        
        await callback.message.edit_text(
            f"✅ Конкурс \"{contest.name}\" завершен!\n\n"
            f"Участников: {len(contest.participants)}\n"
            f"Победители будут объявлены отдельно."
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_contest_"))
async def delete_contest(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав")
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if contest:
        db.delete_contest(contest_id)
        await callback.message.edit_text(f"✅ Конкурс \"{contest.name}\" удален!")
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_contests_admin")
async def back_to_contests_admin(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎁 *Управление конкурсами*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_contests_inline_keyboard()
    )
    await callback.answer()

# ==================== ОБРАБОТЧИКИ ОТЗЫВОВ ====================

@dp.message(F.text == "⭐ Отзывы")
async def reviews_menu(message: Message):
    if is_group_chat(message):
        return
    
    await message.answer(
        "⭐ *Раздел отзывов*\n\n"
        "Здесь вы можете оставить отзыв о покупке или прочитать отзывы других покупателей.",
        parse_mode="Markdown",
        reply_markup=get_reviews_keyboard()
    )

@dp.message(F.text == "📝 Написать отзыв")
async def write_review_start(message: Message, state: FSMContext):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    orders = db.get_user_orders(user_id)
    completed_orders = [o for o in orders if o.status == OrderStatus.COMPLETED]
    
    if not completed_orders:
        await message.answer(
            "❌ У вас нет завершенных заказов, чтобы оставить отзыв.",
            reply_markup=get_reviews_keyboard()
        )
        return
    
    orders_without_review = []
    for order in completed_orders:
        if not db.get_order_review(order.order_id):
            orders_without_review.append(order)
    
    if not orders_without_review:
        await message.answer(
            "✅ Вы уже оставили отзывы на все свои завершенные заказы!",
            reply_markup=get_reviews_keyboard()
        )
        return
    
    builder = InlineKeyboardBuilder()
    for order in orders_without_review[:5]:
        builder.row(InlineKeyboardButton(
            text=f"📦 Заказ #{order.order_id} - {order.created_at.strftime('%d.%m.%Y')}",
            callback_data=f"select_order_review_{order.order_id}"
        ))
    
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_reviews"))
    
    await message.answer(
        "Выберите заказ, о котором хотите оставить отзыв:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("select_order_review_"))
async def select_order_for_review(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split("_")[3]
    order = db.get_order(order_id)
    
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    await state.set_state(UserStates.waiting_for_review_rating)
    await state.update_data(review_order_id=order_id)
    
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.row(InlineKeyboardButton(
            text="⭐" * i,
            callback_data=f"rating_{i}"
        ))
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="cancel_review"))
    
    try:
        await callback.message.edit_text(
            f"Оцените ваш заказ #{order_id} от 1 до 5 звезд:",
            reply_markup=builder.as_markup()
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            f"Оцените ваш заказ #{order_id} от 1 до 5 звезд:",
            reply_markup=builder.as_markup()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("rating_"))
async def process_review_rating(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.split("_")[1])
    
    await state.update_data(review_rating=rating)
    await state.set_state(UserStates.waiting_for_review_comment)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📝 Пропустить", callback_data="skip_comment"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_review"))
    
    try:
        await callback.message.edit_text(
            f"Вы выбрали {rating} ⭐\n\n"
            f"Напишите ваш отзыв (можно оставить пустым, нажав кнопку ниже):",
            reply_markup=builder.as_markup()
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            f"Вы выбрали {rating} ⭐\n\n"
            f"Напишите ваш отзыв (можно оставить пустым, нажав кнопку ниже):",
            reply_markup=builder.as_markup()
        )
    await callback.answer()

@dp.callback_query(F.data == "skip_comment")
async def skip_review_comment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('review_order_id')
    rating = data.get('review_rating')
    
    await save_review(callback.from_user.id, callback.from_user.username, order_id, rating, "", callback.message)
    await state.clear()

@dp.message(UserStates.waiting_for_review_comment)
async def process_review_comment(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_reviews_keyboard()
        )
        return
    
    data = await state.get_data()
    order_id = data.get('review_order_id')
    rating = data.get('review_rating')
    
    await save_review(message.from_user.id, message.from_user.username, order_id, rating, message.text, message)
    await state.clear()

async def save_review(user_id: int, username: str, order_id: str, rating: int, comment: str, message_obj: Message):
    review = db.add_review(user_id, username, order_id, rating, comment)
    
    stars = "⭐" * rating
    review_text = (
        f"⭐ *Новый отзыв!*\n\n"
        f"👤 *Пользователь:* @{username}\n"
        f"📦 *Заказ:* #{order_id}\n"
        f"⭐ *Оценка:* {stars}\n"
    )
    
    if comment:
        review_text += f"💬 *Комментарий:*\n{comment}\n"
    
    review_text += f"📅 *Дата:* {review.created_at.strftime('%d.%m.%Y %H:%M')}"
    
    group_id = db.get_reviews_group()
    if group_id:
        try:
            await bot.send_message(
                group_id,
                review_text,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send review to group: {e}")
    
    await message_obj.answer(
        f"✅ Спасибо за ваш отзыв!\n\n{review_text}",
        parse_mode="Markdown",
        reply_markup=get_reviews_keyboard()
    )

@dp.message(F.text == "📖 Все отзывы")
async def show_all_reviews(message: Message):
    if is_group_chat(message):
        return
    
    reviews = db.get_reviews(limit=20)
    
    if not reviews:
        await message.answer(
            "📭 Пока нет ни одного отзыва. Будьте первым!",
            reply_markup=get_reviews_keyboard()
        )
        return
    
    await message.answer(
        "⭐ *Все отзывы*\n\nВыберите отзыв для просмотра:",
        parse_mode="Markdown",
        reply_markup=get_reviews_inline_keyboard()
    )

@dp.callback_query(F.data.startswith("reviews_page_"))
async def reviews_pagination(callback: CallbackQuery):
    page = int(callback.data.split("_")[2])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_reviews_inline_keyboard(page)
        )
    except TelegramBadRequest:
        pass
    await callback.answer()

@dp.callback_query(F.data.startswith("view_review_"))
async def view_review(callback: CallbackQuery):
    review_id = callback.data.split("_")[2]
    
    review = None
    for r in db.reviews:
        if r.review_id == review_id:
            review = r
            break
    
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    
    stars = "⭐" * review.rating
    review_text = (
        f"⭐ *Отзыв #{review.review_id}*\n\n"
        f"👤 *Пользователь:* @{review.username}\n"
        f"📦 *Заказ:* #{review.order_id}\n"
        f"⭐ *Оценка:* {stars}\n"
    )
    
    if review.comment:
        review_text += f"💬 *Комментарий:*\n{review.comment}\n"
    
    review_text += f"📅 *Дата:* {review.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    
    if review.admin_reply:
        review_text += f"\n👨‍💼 *Ответ администратора:*\n{review.admin_reply}\n"
        review_text += f"📅 *Ответ дан:* {review.admin_reply_at.strftime('%d.%m.%Y %H:%M')}"
    
    keyboard = None
    if is_admin(callback.from_user.id) and not review.admin_reply:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"reply_review_{review.review_id}")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_reviews_list")]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_reviews_list")]
            ]
        )
    
    try:
        await callback.message.edit_text(
            review_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            review_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    await callback.answer()

@dp.callback_query(F.data == "back_to_reviews_list")
async def back_to_reviews_list(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "⭐ *Все отзывы*\n\nВыберите отзыв для просмотра:",
            parse_mode="Markdown",
            reply_markup=get_reviews_inline_keyboard()
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            "⭐ *Все отзывы*\n\nВыберите отзыв для просмотра:",
            parse_mode="Markdown",
            reply_markup=get_reviews_inline_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data == "back_to_reviews")
async def back_to_reviews(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "⭐ *Раздел отзывов*",
        parse_mode="Markdown",
        reply_markup=get_reviews_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "cancel_review")
async def cancel_review(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer(
        "⭐ *Раздел отзывов*",
        parse_mode="Markdown",
        reply_markup=get_reviews_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("review_order_"))
async def review_from_order(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    await state.set_state(UserStates.waiting_for_review_rating)
    await state.update_data(review_order_id=order_id)
    
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.row(InlineKeyboardButton(
            text="⭐" * i,
            callback_data=f"rating_{i}"
        ))
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="cancel_review"))
    
    await callback.message.answer(
        f"Оцените ваш заказ #{order_id} от 1 до 5 звезд:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# ==================== ОБРАБОТЧИКИ КАТАЛОГА ====================

@dp.callback_query(F.data.startswith("page_"))
async def process_pagination(callback: CallbackQuery):
    page = int(callback.data.split("_")[1])
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_products_inline_keyboard(page)
        )
    except TelegramBadRequest:
        pass
    await callback.answer()

@dp.callback_query(F.data.startswith("product_"))
async def show_product(callback: CallbackQuery):
    product_id = callback.data.split("_")[1]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
    stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
    
    text = (
        f"*{product.name}*\n\n"
        f"{product.description}\n\n"
        f"💰 Цена: {rub_price}₽ / {stars_price}⭐\n"
        f"📦 В наличии: {product.stock} шт.\n\n"
        f"Выберите количество (текущее: 1):"
    )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_product_inline_keyboard(product_id, 1)
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=get_product_inline_keyboard(product_id, 1)
        )
    await callback.answer()

@dp.callback_query(F.data == "back_to_catalog")
async def back_to_catalog(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🛍 Наш каталог:",
            reply_markup=get_products_inline_keyboard()
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            "🛍 Наш каталог:",
            reply_markup=get_products_inline_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("inc_"))
async def increase_quantity(callback: CallbackQuery):
    product_id = callback.data.split("_")[1]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    current_qty = 1
    if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
        for row in callback.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "show_qty":
                    try:
                        current_qty = int(btn.text)
                    except:
                        current_qty = 1
                    break
    
    if current_qty < product.stock:
        current_qty += 1
    
    rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
    stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
    
    text = (
        f"*{product.name}*\n\n"
        f"{product.description}\n\n"
        f"💰 Цена: {rub_price}₽ / {stars_price}⭐\n"
        f"📦 В наличии: {product.stock} шт.\n\n"
        f"Выберите количество (текущее: {current_qty}):"
    )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_product_inline_keyboard(product_id, current_qty)
        )
    except TelegramBadRequest:
        pass
    
    await callback.answer()

@dp.callback_query(F.data.startswith("dec_"))
async def decrease_quantity(callback: CallbackQuery):
    product_id = callback.data.split("_")[1]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    current_qty = 1
    if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
        for row in callback.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "show_qty":
                    try:
                        current_qty = int(btn.text)
                    except:
                        current_qty = 1
                    break
    
    if current_qty > 1:
        current_qty -= 1
    
    rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
    stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
    
    text = (
        f"*{product.name}*\n\n"
        f"{product.description}\n\n"
        f"💰 Цена: {rub_price}₽ / {stars_price}⭐\n"
        f"📦 В наличии: {product.stock} шт.\n\n"
        f"Выберите количество (текущее: {current_qty}):"
    )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_product_inline_keyboard(product_id, current_qty)
        )
    except TelegramBadRequest:
        pass
    
    await callback.answer()

@dp.callback_query(F.data == "show_qty")
async def show_quantity(callback: CallbackQuery):
    await callback.answer(f"Текущее количество: {callback.message.text.split('текущее: ')[-1].rstrip('):') if 'текущее:' in callback.message.text else '1'}", show_alert=False)

@dp.callback_query(F.data.startswith("manual_qty_"))
async def manual_quantity_prompt(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split("_")[2]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    await state.set_state(UserStates.waiting_for_manual_quantity)
    await state.update_data(product_id=product_id)
    
    await callback.message.answer(
        f"Введите желаемое количество товара *{product.name}* (доступно: {product.stock} шт.):",
        parse_mode="Markdown",
        reply_markup=get_manual_quantity_inline_keyboard(product_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("cancel_manual_qty_"))
async def cancel_manual_quantity(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    product_id = callback.data.split("_")[3]
    product = db.get_product(product_id)
    
    if product:
        rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
        stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
        
        text = (
            f"*{product.name}*\n\n"
            f"{product.description}\n\n"
            f"💰 Цена: {rub_price}₽ / {stars_price}⭐\n"
            f"📦 В наличии: {product.stock} шт.\n\n"
            f"Выберите количество (текущее: 1):"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_product_inline_keyboard(product_id, 1)
        )
    else:
        await callback.message.delete()
        await callback.message.answer("❌ Товар не найден")
    
    await callback.answer()

@dp.message(UserStates.waiting_for_manual_quantity)
async def process_manual_quantity(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get('product_id')
    product = db.get_product(product_id)
    
    if not product:
        await message.answer("❌ Товар не найден")
        await state.clear()
        return
    
    try:
        quantity = int(message.text)
        if quantity < 1:
            await message.answer(
                "❌ Количество должно быть больше 0",
                reply_markup=get_manual_quantity_inline_keyboard(product_id)
            )
            return
        if quantity > product.stock:
            await message.answer(
                f"❌ Доступно только {product.stock} шт.",
                reply_markup=get_manual_quantity_inline_keyboard(product_id)
            )
            return
        
        rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
        stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
        
        text = (
            f"*{product.name}*\n\n"
            f"{product.description}\n\n"
            f"💰 Цена: {rub_price}₽ / {stars_price}⭐\n"
            f"📦 В наличии: {product.stock} шт.\n\n"
            f"Выберите количество (текущее: {quantity}):"
        )
        
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=get_product_inline_keyboard(product_id, quantity)
        )
        
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Введите целое число",
            reply_markup=get_manual_quantity_inline_keyboard(product_id)
        )

@dp.callback_query(F.data.startswith("add_"))
async def add_to_cart(callback: CallbackQuery):
    product_id = callback.data.split("_")[1]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    current_qty = 1
    if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
        for row in callback.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "show_qty":
                    try:
                        current_qty = int(btn.text)
                    except:
                        current_qty = 1
                    break
    
    if product.stock < current_qty:
        await callback.answer(
            f"Недостаточно товара. В наличии: {product.stock}",
            show_alert=True
        )
        return
    
    db.add_to_cart(callback.from_user.id, product_id, current_qty)
    
    await callback.answer(f"✅ {product.name} x{current_qty} добавлен в корзину")
    
    try:
        await callback.message.edit_text(
            "🛍 Наш каталог:",
            reply_markup=get_products_inline_keyboard()
        )
    except:
        await callback.message.delete()
        await callback.message.answer(
            "🛍 Наш каталог:",
            reply_markup=get_products_inline_keyboard()
        )

@dp.callback_query(F.data == "view_cart")
async def view_cart_from_catalog(callback: CallbackQuery):
    user_id = callback.from_user.id
    cart = db.get_cart(user_id)
    
    if not cart.items:
        await callback.answer("Корзина пуста", show_alert=True)
        return
    
    cart_text = "🛒 *Ваша корзина:*\n\n"
    total_rub = 0.0
    total_stars = 0.0
    items_details = []
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            item_rub = product.price_rub * quantity
            item_stars = product.price_stars * quantity
            items_details.append({
                'name': product.name,
                'quantity': quantity,
                'price_rub': product.price_rub,
                'price_stars': product.price_stars,
                'total_rub': item_rub,
                'total_stars': item_stars
            })
            total_rub += item_rub
            total_stars += item_stars
    
    original_rub = total_rub
    original_stars = total_stars
    
    if cart.discount > 0:
        total_rub = total_rub * (100 - cart.discount) / 100
        total_stars = total_stars * (100 - cart.discount) / 100
    
    for item in items_details:
        rub_price = f"{item['price_rub']:.2f}".rstrip('0').rstrip('.') if item['price_rub'] % 1 else str(int(item['price_rub']))
        stars_price = f"{item['price_stars']:.2f}".rstrip('0').rstrip('.') if item['price_stars'] % 1 else str(int(item['price_stars']))
        item_total_rub = f"{item['total_rub']:.2f}".rstrip('0').rstrip('.') if item['total_rub'] % 1 else str(int(item['total_rub']))
        item_total_stars = f"{item['total_stars']:.2f}".rstrip('0').rstrip('.') if item['total_stars'] % 1 else str(int(item['total_stars']))
        
        cart_text += f"• {item['name']} x{item['quantity']}\n"
        cart_text += f"  {rub_price}₽ x{item['quantity']} = {item_total_rub}₽\n"
        cart_text += f"  {stars_price}⭐ x{item['quantity']} = {item_total_stars}⭐\n\n"
    
    if cart.discount > 0:
        original_rub_str = f"{original_rub:.2f}".rstrip('0').rstrip('.') if original_rub % 1 else str(int(original_rub))
        original_stars_str = f"{original_stars:.2f}".rstrip('0').rstrip('.') if original_stars % 1 else str(int(original_stars))
        total_rub_str = f"{total_rub:.2f}".rstrip('0').rstrip('.') if total_rub % 1 else str(int(total_rub))
        total_stars_str = f"{total_stars:.2f}".rstrip('0').rstrip('.') if total_stars % 1 else str(int(total_stars))
        
        cart_text += f"*Скидка:* {cart.discount}%\n"
        cart_text += f"*Итого со скидкой:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    else:
        total_rub_str = f"{total_rub:.2f}".rstrip('0').rstrip('.') if total_rub % 1 else str(int(total_rub))
        total_stars_str = f"{total_stars:.2f}".rstrip('0').rstrip('.') if total_stars % 1 else str(int(total_stars))
        cart_text += f"*Итого:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    
    if cart.promocode:
        cart_text += f"\n*Применен промокод:* {cart.promocode}"
    
    await callback.message.answer(
        cart_text,
        parse_mode="Markdown",
        reply_markup=get_cart_inline_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("remove_"))
async def remove_from_cart(callback: CallbackQuery):
    product_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    db.remove_from_cart(user_id, product_id)
    
    await callback.answer("Товар удален из корзины")
    
    cart = db.get_cart(user_id)
    if not cart.items:
        try:
            await callback.message.edit_text("🛒 Ваша корзина пуста.")
        except:
            await callback.message.delete()
            await callback.message.answer("🛒 Ваша корзина пуста.")
        return
    
    cart_text = "🛒 *Ваша корзина:*\n\n"
    total_rub = 0.0
    total_stars = 0.0
    items_details = []
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            item_rub = product.price_rub * quantity
            item_stars = product.price_stars * quantity
            items_details.append({
                'name': product.name,
                'quantity': quantity,
                'price_rub': product.price_rub,
                'price_stars': product.price_stars,
                'total_rub': item_rub,
                'total_stars': item_stars
            })
            total_rub += item_rub
            total_stars += item_stars
    
    original_rub = total_rub
    original_stars = total_stars
    
    if cart.discount > 0:
        total_rub = total_rub * (100 - cart.discount) / 100
        total_stars = total_stars * (100 - cart.discount) / 100
    
    for item in items_details:
        rub_price = f"{item['price_rub']:.2f}".rstrip('0').rstrip('.') if item['price_rub'] % 1 else str(int(item['price_rub']))
        stars_price = f"{item['price_stars']:.2f}".rstrip('0').rstrip('.') if item['price_stars'] % 1 else str(int(item['price_stars']))
        item_total_rub = f"{item['total_rub']:.2f}".rstrip('0').rstrip('.') if item['total_rub'] % 1 else str(int(item['total_rub']))
        item_total_stars = f"{item['total_stars']:.2f}".rstrip('0').rstrip('.') if item['total_stars'] % 1 else str(int(item['total_stars']))
        
        cart_text += f"• {item['name']} x{item['quantity']}\n"
        cart_text += f"  {rub_price}₽ x{item['quantity']} = {item_total_rub}₽\n"
        cart_text += f"  {stars_price}⭐ x{item['quantity']} = {item_total_stars}⭐\n\n"
    
    if cart.discount > 0:
        original_rub_str = f"{original_rub:.2f}".rstrip('0').rstrip('.') if original_rub % 1 else str(int(original_rub))
        original_stars_str = f"{original_stars:.2f}".rstrip('0').rstrip('.') if original_stars % 1 else str(int(original_stars))
        total_rub_str = f"{total_rub:.2f}".rstrip('0').rstrip('.') if total_rub % 1 else str(int(total_rub))
        total_stars_str = f"{total_stars:.2f}".rstrip('0').rstrip('.') if total_stars % 1 else str(int(total_stars))
        
        cart_text += f"*Скидка:* {cart.discount}%\n"
        cart_text += f"*Итого со скидкой:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    else:
        total_rub_str = f"{total_rub:.2f}".rstrip('0').rstrip('.') if total_rub % 1 else str(int(total_rub))
        total_stars_str = f"{total_stars:.2f}".rstrip('0').rstrip('.') if total_stars % 1 else str(int(total_stars))
        cart_text += f"*Итого:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    
    try:
        await callback.message.edit_text(
            cart_text,
            parse_mode="Markdown",
            reply_markup=get_cart_inline_keyboard(user_id)
        )
    except:
        await callback.message.delete()
        await callback.message.answer(
            cart_text,
            parse_mode="Markdown",
            reply_markup=get_cart_inline_keyboard(user_id)
        )

@dp.callback_query(F.data == "clear_cart")
async def clear_cart_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    db.clear_cart(user_id)
    
    await callback.answer("Корзина очищена")
    try:
        await callback.message.edit_text("🛒 Ваша корзина пуста.")
    except:
        await callback.message.delete()
        await callback.message.answer("🛒 Ваша корзина пуста.")

# ==================== ОБРАБОТЧИКИ ОПЛАТЫ ====================

@dp.callback_query(F.data == "pay_stars")
async def pay_with_stars(callback: CallbackQuery):
    user_id = callback.from_user.id
    cart = db.get_cart(user_id)
    username = callback.from_user.username or "NoUsername"
    
    if not cart.items:
        await callback.answer("Корзина пуста", show_alert=True)
        return
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if not product or product.stock < quantity:
            await callback.answer(
                f"Товар {product.name if product else 'Неизвестный'} недоступен в нужном количестве",
                show_alert=True
            )
            return
    
    order = db.create_order(user_id, username, cart, PaymentMethod.STARS)
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            product.stock -= quantity
    
    stars_amount = int(order.total_stars)
    
    prices = [LabeledPrice(label="Оплата товаров", amount=stars_amount)]
    
    await callback.message.answer_invoice(
        title=f"Заказ #{order.order_id}",
        description=f"Оплата заказа на сумму {stars_amount} ⭐",
        payload=order.order_id,
        provider_token="",
        currency="XTR",
        prices=prices,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💰 Оплатить", pay=True)]
            ]
        )
    )
    
    db.clear_cart(user_id)
    
    await callback.answer()

@dp.callback_query(F.data == "pay_rubles")
async def pay_with_rubles(callback: CallbackQuery):
    user_id = callback.from_user.id
    cart = db.get_cart(user_id)
    username = callback.from_user.username or "NoUsername"
    
    if not cart.items:
        await callback.answer("Корзина пуста", show_alert=True)
        return
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if not product or product.stock < quantity:
            await callback.answer(
                f"Товар {product.name if product else 'Неизвестный'} недоступен в нужном количестве",
                show_alert=True
            )
            return
    
    order = db.create_order(user_id, username, cart, PaymentMethod.RUBLES)
    
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            product.stock -= quantity
    
    payment_details = db.get_payment_details()
    
    total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
    
    text = (
        f"🧾 *Заказ #{order.order_id}*\n\n"
        f"Сумма к оплате: {total_rub_str}₽\n\n"
        f"*Реквизиты для перевода:*\n"
        f"💳 Карта: `{payment_details['card']}`\n"
        f"📱 Телефон: `{payment_details['phone']}`\n"
        f"🏦 Банк: {payment_details['bank']}\n\n"
        f"После оплаты нажмите кнопку ниже и отправьте скриншот подтверждения оплаты."
    )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📸 Отправить скрин", callback_data=f"send_screen_{order.order_id}")],
                    [InlineKeyboardButton(text="◀️ На главную", callback_data="back_to_main")]
                ]
            )
        )
    except:
        await callback.message.delete()
        await callback.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📸 Отправить скрин", callback_data=f"send_screen_{order.order_id}")],
                    [InlineKeyboardButton(text="◀️ На главную", callback_data="back_to_main")]
                ]
            )
        )
    
    db.clear_cart(user_id)
    
    await callback.answer()

@dp.callback_query(F.data.startswith("send_screen_"))
async def send_screenshot_prompt(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    if order.status != OrderStatus.PENDING:
        await callback.answer("Этот заказ уже обработан", show_alert=True)
        return
    
    await state.set_state(UserStates.waiting_for_screenshot)
    await state.update_data(order_id=order_id)
    
    await callback.message.answer(
        f"📸 Отправьте скриншот подтверждения оплаты для заказа #{order_id}",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )
    await callback.answer()

@dp.message(UserStates.waiting_for_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    order = db.get_order(order_id)
    
    if not order:
        await message.answer("❌ Заказ не найден")
        await state.clear()
        return
    
    file_id = message.photo[-1].file_id
    db.set_order_screenshot(order_id, file_id)
    
    payment_details = db.get_payment_details()
    
    items_text = ""
    for product_id, quantity in order.items.items():
        product = db.get_product(product_id)
        if product:
            items_text += f"• {product.name} x{quantity}\n"
    
    total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
    
    admin_text = (
        f"📸 *Новый скрин оплаты!*\n\n"
        f"🧾 Заказ: #{order_id}\n"
        f"👤 Пользователь: @{order.username} (ID: {order.user_id})\n"
        f"💰 Сумма: {total_rub_str}₽\n"
        f"📦 Товары:\n{items_text}\n"
        f"💳 Реквизиты: {payment_details['card']} / {payment_details['phone']}\n\n"
        f"Проверьте оплату и подтвердите заказ в админ-панели."
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                file_id,
                caption=admin_text,
                parse_mode="Markdown",
                reply_markup=get_order_actions_inline_keyboard(order_id, is_admin=True)
            )
        except Exception as e:
            logger.error(f"Failed to send screenshot to admin {admin_id}: {e}")
    
    await message.answer(
        f"✅ Скриншот отправлен на проверку. Мы уведомим вас о подтверждении заказа.",
        reply_markup=get_main_keyboard(is_admin(message.from_user.id))
    )
    
    await state.clear()

@dp.message(UserStates.waiting_for_screenshot)
async def invalid_screenshot(message: Message):
    await message.answer(
        "❌ Пожалуйста, отправьте фото (скриншот оплаты).",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    order_id = message.successful_payment.invoice_payload
    order = db.get_order(order_id)
    
    if order:
        order.status = OrderStatus.PAID
        
        items_text = ""
        for product_id, quantity in order.items.items():
            product = db.get_product(product_id)
            if product:
                items_text += f"• {product.name} x{quantity}\n"
        
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        receipt = (
            f"🧾 *ЧЕК #{order.order_id}*\n"
            f"✅ Заказ успешно оплачен звездами!\n\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"💰 Сумма: {total_stars_str} ⭐\n"
            f"📦 Товары:\n{items_text}\n"
            f"👨‍💼 Продавец: {db.get_bot_username()}\n"
        )
        
        if order.promocode_used:
            receipt += f"🏷 Промокод: {order.promocode_used}\n"
        
        receipt += f"\nСпасибо за покупку!"
        
        await message.answer(receipt, parse_mode="Markdown")
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"💰 *Оплата звездами*\n\n"
                    f"Заказ #{order_id} оплачен!\n"
                    f"Пользователь: @{order.username}\n"
                    f"Сумма: {total_stars_str} ⭐",
                    parse_mode="Markdown",
                    reply_markup=get_order_actions_inline_keyboard(order_id, is_admin=True)
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

# ==================== ОБРАБОТЧИКИ ОТМЕНЫ ====================

@dp.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    
    await callback.message.delete()
    await callback.message.answer(
        "✅ Действие отменено.",
        reply_markup=get_main_keyboard(is_admin(callback.from_user.id))
    )
    await callback.answer()

# ==================== ОБРАБОТЧИКИ АДМИНА ====================

@dp.message(F.text == "⚙️ Админ панель")
async def admin_panel(message: Message):
    if is_group_chat(message):
        return
    
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора.")
        return
    
    await message.answer(
        "⚙️ Админ панель",
        reply_markup=get_admin_keyboard()
    )

@dp.message(F.text == "🔙 На главную")
async def back_to_main(message: Message):
    if is_group_chat(message):
        return
    
    await message.answer(
        "Главное меню",
        reply_markup=get_main_keyboard(is_admin(message.from_user.id))
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "Главное меню",
        reply_markup=get_main_keyboard(is_admin(callback.from_user.id))
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_callback(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "⚙️ Админ панель",
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()

# ===== Управление именем продавца =====

@dp.message(F.text == "👤 Установить имя")
async def set_bot_username_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_bot_username)
    await message.answer(
        "Введите имя продавца/магазина, которое будет отображаться в чеках:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_bot_username)
async def set_bot_username_process(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    db.set_bot_username(message.text)
    
    await message.answer(
        f"✅ Имя продавца установлено: {message.text}",
        reply_markup=get_admin_keyboard()
    )
    await state.clear()

# ===== Управление отзывами (админка) =====

@dp.message(F.text == "⭐ Управление отзывами")
async def manage_reviews(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    reviews = db.get_reviews(limit=20)
    
    if not reviews:
        await message.answer("📭 Пока нет ни одного отзыва.")
        return
    
    builder = InlineKeyboardBuilder()
    for review in reviews[:10]:
        stars = "⭐" * review.rating
        status = "✅" if review.admin_reply else "⏳"
        builder.row(InlineKeyboardButton(
            text=f"{status} {stars} - @{review.username} - {review.created_at.strftime('%d.%m.%Y')}",
            callback_data=f"admin_review_{review.review_id}"
        ))
    
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await message.answer(
        "⭐ *Управление отзывами*\n\nВыберите отзыв для ответа:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("admin_review_"))
async def admin_view_review(callback: CallbackQuery):
    review_id = callback.data.split("_")[2]
    
    review = None
    for r in db.reviews:
        if r.review_id == review_id:
            review = r
            break
    
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    
    stars = "⭐" * review.rating
    review_text = (
        f"⭐ *Отзыв #{review.review_id}*\n\n"
        f"👤 *Пользователь:* @{review.username} (ID: {review.user_id})\n"
        f"📦 *Заказ:* #{review.order_id}\n"
        f"⭐ *Оценка:* {stars}\n"
    )
    
    if review.comment:
        review_text += f"💬 *Комментарий:*\n{review.comment}\n"
    
    review_text += f"📅 *Дата:* {review.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    
    if review.admin_reply:
        review_text += f"\n👨‍💼 *Ваш ответ:*\n{review.admin_reply}\n"
        review_text += f"📅 *Ответ дан:* {review.admin_reply_at.strftime('%d.%m.%Y %H:%M')}"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"reply_review_{review.review_id}")] if not review.admin_reply else [],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_reviews_admin")]
        ]
    )
    
    try:
        await callback.message.edit_text(
            review_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            review_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("reply_review_"))
async def reply_to_review_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    review_id = callback.data.split("_")[2]
    
    review = None
    for r in db.reviews:
        if r.review_id == review_id:
            review = r
            break
    
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_review_reply)
    await state.update_data(review_id=review_id, user_id=review.user_id)
    
    await callback.message.answer(
        f"✏️ Введите ответ на отзыв от @{review.username}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_review_reply)
async def process_review_reply(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    data = await state.get_data()
    review_id = data['review_id']
    user_id = data['user_id']
    
    db.add_admin_reply_to_review(review_id, message.text)
    
    try:
        await bot.send_message(
            user_id,
            f"👨‍💼 *Ответ администратора на ваш отзыв*\n\n"
            f"{message.text}",
            parse_mode="Markdown"
        )
        
        await message.answer(
            f"✅ Ответ на отзыв отправлен пользователю!",
            reply_markup=get_admin_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send review reply to user {user_id}: {e}")
        await message.answer(
            "❌ Не удалось отправить ответ пользователю. Возможно, пользователь заблокировал бота.",
            reply_markup=get_admin_keyboard()
        )
    
    group_id = db.get_reviews_group()
    if group_id:
        try:
            await bot.send_message(
                group_id,
                f"👨‍💼 *Ответ администратора на отзыв*\n\n{message.text}"
            )
        except:
            pass
    
    await state.clear()

@dp.callback_query(F.data == "back_to_reviews_admin")
async def back_to_reviews_admin(callback: CallbackQuery):
    await callback.message.delete()
    await manage_reviews(callback.message)
    await callback.answer()

# ===== Управление товарами =====

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_product_name)
    await message.answer(
        "Введите название товара:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_product_name)
async def add_product_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.waiting_for_product_description)
    await message.answer(
        "Введите описание товара:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_product_description)
async def add_product_description(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(description=message.text)
    await state.set_state(AdminStates.waiting_for_product_price_rub)
    await message.answer(
        "Введите цену в рублях (можно использовать дробные числа, например 99.99):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_product_price_rub)
async def add_product_price_rub(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        price = float(message.text.replace(',', '.'))
        if price < 0:
            raise ValueError
        await state.update_data(price_rub=price)
        await state.set_state(AdminStates.waiting_for_product_price_stars)
        await message.answer(
            "Введите цену в звездах (можно использовать дробные числа, например 9.99):",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число (можно дробное)",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

@dp.message(AdminStates.waiting_for_product_price_stars)
async def add_product_price_stars(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        price = float(message.text.replace(',', '.'))
        if price < 0:
            raise ValueError
        await state.update_data(price_stars=price)
        await state.set_state(AdminStates.waiting_for_product_stock)
        await message.answer(
            "Введите количество на складе (целое число):",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число (можно дробное)",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

@dp.message(AdminStates.waiting_for_product_stock)
async def add_product_stock(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        stock = int(message.text)
        if stock < 0:
            raise ValueError
        
        data = await state.get_data()
        product = db.add_product(
            data['name'],
            data['description'],
            data['price_rub'],
            data['price_stars'],
            stock
        )
        
        rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
        stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
        
        await message.answer(
            f"✅ Товар успешно добавлен!\n\n"
            f"ID: {product.id}\n"
            f"Название: {product.name}\n"
            f"Цена: {rub_price}₽ / {stars_price}⭐\n"
            f"В наличии: {product.stock}",
            reply_markup=get_admin_keyboard()
        )
        
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Введите корректное целое число",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

# ===== Редактирование товаров =====

@dp.message(F.text == "✏️ Редактировать товар")
async def edit_product_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    products = db.get_all_products()
    if not products:
        await message.answer("📭 Нет товаров для редактирования.")
        return
    
    builder = InlineKeyboardBuilder()
    for product in products:
        builder.row(InlineKeyboardButton(
            text=f"{product.name} (ID: {product.id})",
            callback_data=f"edit_select_{product.id}"
        ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    
    await message.answer(
        "Выберите товар для редактирования:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_product_id_to_edit)

@dp.callback_query(F.data.startswith("edit_select_"))
async def edit_product_select(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split("_")[2]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    await state.update_data(edit_product_id=product_id)
    await state.set_state(AdminStates.waiting_for_product_edit_field)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📝 Название", callback_data="edit_field_name"))
    builder.row(InlineKeyboardButton(text="📄 Описание", callback_data="edit_field_description"))
    builder.row(InlineKeyboardButton(text="💰 Цена (рубли)", callback_data="edit_field_price_rub"))
    builder.row(InlineKeyboardButton(text="⭐ Цена (звезды)", callback_data="edit_field_price_stars"))
    builder.row(InlineKeyboardButton(text="📦 Количество", callback_data="edit_field_stock"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    
    await callback.message.edit_text(
        f"Редактирование товара *{product.name}*\n\n"
        f"Выберите поле для редактирования:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_field_"))
async def edit_product_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split("_")[2]
    await state.update_data(edit_field=field)
    await state.set_state(AdminStates.waiting_for_product_edit_value)
    
    field_names = {
        "name": "название",
        "description": "описание",
        "price_rub": "цену в рублях",
        "price_stars": "цену в звездах",
        "stock": "количество на складе"
    }
    
    await callback.message.answer(
        f"Введите новое {field_names.get(field, field)}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_product_edit_value)
async def process_product_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data['edit_product_id']
    field = data['edit_field']
    value = message.text
    
    product = db.get_product(product_id)
    if not product:
        await message.answer("❌ Товар не найден")
        await state.clear()
        return
    
    try:
        if field == "name":
            product.name = value
        elif field == "description":
            product.description = value
        elif field == "price_rub":
            product.price_rub = float(value.replace(',', '.'))
        elif field == "price_stars":
            product.price_stars = float(value.replace(',', '.'))
        elif field == "stock":
            product.stock = int(value)
        
        rub_price = f"{product.price_rub:.2f}".rstrip('0').rstrip('.') if product.price_rub % 1 else str(int(product.price_rub))
        stars_price = f"{product.price_stars:.2f}".rstrip('0').rstrip('.') if product.price_stars % 1 else str(int(product.price_stars))
        
        await message.answer(
            f"✅ Товар обновлен!\n\n"
            f"ID: {product.id}\n"
            f"Название: {product.name}\n"
            f"Цена: {rub_price}₽ / {stars_price}⭐\n"
            f"В наличии: {product.stock}",
            reply_markup=get_admin_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Неверный формат данных. Попробуйте снова.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

# ===== Удаление товаров =====

@dp.message(F.text == "❌ Удалить товар")
async def delete_product_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    products = db.get_all_products()
    if not products:
        await message.answer("📭 Нет товаров для удаления.")
        return
    
    builder = InlineKeyboardBuilder()
    for product in products:
        builder.row(InlineKeyboardButton(
            text=f"❌ {product.name} (ID: {product.id})",
            callback_data=f"delete_confirm_{product.id}"
        ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    
    await message.answer(
        "Выберите товар для удаления:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_product_id_to_delete)

@dp.callback_query(F.data.startswith("delete_confirm_"))
async def delete_product_confirm(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split("_")[2]
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    db.delete_product(product_id)
    
    await callback.message.edit_text(f"✅ Товар \"{product.name}\" удален!")
    await callback.answer()
    await state.clear()

# ===== Управление промокодами =====

@dp.message(F.text == "🎫 Создать промокод")
async def create_promocode_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_promocode_code)
    await message.answer(
        "Введите код промокода:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_promocode_code)
async def create_promocode_code(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(promo_code=message.text.upper())
    await state.set_state(AdminStates.waiting_for_promocode_discount)
    await message.answer(
        "Введите процент скидки (1-100):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )

@dp.message(AdminStates.waiting_for_promocode_discount)
async def create_promocode_discount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        discount = int(message.text)
        if discount < 1 or discount > 100:
            raise ValueError
        
        await state.update_data(promo_discount=discount)
        await state.set_state(AdminStates.waiting_for_promocode_days)
        await message.answer(
            "Введите количество дней действия промокода:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите число от 1 до 100",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

@dp.message(AdminStates.waiting_for_promocode_days)
async def create_promocode_days(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        days = int(message.text)
        if days < 1:
            raise ValueError
        
        await state.update_data(promo_days=days)
        await state.set_state(AdminStates.waiting_for_promocode_uses)
        await message.answer(
            "Введите максимальное количество использований:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число дней",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

@dp.message(AdminStates.waiting_for_promocode_uses)
async def create_promocode_uses(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        uses = int(message.text)
        if uses < 1:
            raise ValueError
        
        data = await state.get_data()
        promo = db.add_promocode(data['promo_code'], data['promo_discount'], data['promo_days'], uses)
        
        await message.answer(
            f"✅ Промокод создан!\n\n"
            f"Код: `{promo.code}`\n"
            f"Скидка: {promo.discount_percent}%\n"
            f"Действует: {promo.valid_until.strftime('%d.%m.%Y')}\n"
            f"Использований: {promo.max_uses}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Введите корректное число",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
            )
        )

# ===== Реквизиты оплаты =====

@dp.message(F.text == "💳 Реквизиты оплаты")
async def payment_details_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    current = db.get_payment_details()
    
    text = (
        f"💳 *Текущие реквизиты оплаты:*\n\n"
        f"Карта: `{current['card']}`\n"
        f"Телефон: `{current['phone']}`\n"
        f"Банк: {current['bank']}\n\n"
        f"Выберите, что изменить:"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Карта", callback_data="edit_card")],
            [InlineKeyboardButton(text="📱 Телефон", callback_data="edit_phone")],
            [InlineKeyboardButton(text="🏦 Банк", callback_data="edit_bank")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin")]
        ]
    )
    
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query(F.data == "edit_card")
async def edit_card(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_payment_card)
    await callback.message.answer(
        "Введите номер карты:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "edit_phone")
async def edit_phone(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_payment_phone)
    await callback.message.answer(
        "Введите номер телефона:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "edit_bank")
async def edit_bank(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_payment_bank)
    await callback.message.answer(
        "Введите название банка:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_payment_card)
async def process_card(message: Message, state: FSMContext):
    db.update_payment_details(card=message.text)
    await message.answer(f"✅ Номер карты обновлен: {message.text}", reply_markup=get_admin_keyboard())
    await state.clear()

@dp.message(AdminStates.waiting_for_payment_phone)
async def process_phone(message: Message, state: FSMContext):
    db.update_payment_details(phone=message.text)
    await message.answer(f"✅ Номер телефона обновлен: {message.text}", reply_markup=get_admin_keyboard())
    await state.clear()

@dp.message(AdminStates.waiting_for_payment_bank)
async def process_bank(message: Message, state: FSMContext):
    db.update_payment_details(bank=message.text)
    await message.answer(f"✅ Банк обновлен: {message.text}", reply_markup=get_admin_keyboard())
    await state.clear()

# ===== Статистика =====

@dp.message(F.text == "📊 Статистика")
async def show_statistics(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    total_orders = len(db.orders)
    pending_orders = len(db.get_pending_orders())
    active_orders = len(db.get_active_orders())
    completed_orders = len(db.get_completed_orders())
    total_products = len(db.products)
    total_users = len(set(o.user_id for o in db.orders.values()))
    total_reviews = len(db.reviews)
    active_contests = len(db.get_active_contests())
    
    total_revenue_rub = sum(o.total_rub for o in db.orders.values() if o.status == OrderStatus.COMPLETED)
    total_revenue_stars = sum(o.total_stars for o in db.orders.values() if o.status == OrderStatus.COMPLETED)
    
    total_revenue_rub_str = f"{total_revenue_rub:.2f}".rstrip('0').rstrip('.') if total_revenue_rub % 1 else str(int(total_revenue_rub))
    total_revenue_stars_str = f"{total_revenue_stars:.2f}".rstrip('0').rstrip('.') if total_revenue_stars % 1 else str(int(total_revenue_stars))
    
    text = (
        f"📊 *Статистика магазина*\n\n"
        f"📦 *Заказы:*\n"
        f"  • Всего: {total_orders}\n"
        f"  • Ожидают оплаты: {pending_orders}\n"
        f"  • В обработке: {active_orders}\n"
        f"  • Завершено: {completed_orders}\n\n"
        f"💰 *Выручка:*\n"
        f"  • Рубли: {total_revenue_rub_str}₽\n"
        f"  • Звезды: {total_revenue_stars_str}⭐\n\n"
        f"👥 *Пользователи:* {total_users}\n"
        f"🛍 *Товаров:* {total_products}\n"
        f"⭐ *Отзывов:* {total_reviews}\n"
        f"🎁 *Активных конкурсов:* {active_contests}"
    )
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# ===== Ответы поддержки =====

@dp.message(F.text == "📨 Ответы поддержки")
async def support_tickets_admin(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await message.answer(
        "📨 *Управление обращениями в поддержку*\n\n"
        "Выберите категорию:",
        parse_mode="Markdown",
        reply_markup=get_support_tickets_inline_keyboard()
    )

@dp.callback_query(F.data == "tickets_pending")
async def show_pending_tickets(callback: CallbackQuery):
    await callback.message.edit_text(
        "📨 *Ожидают ответа:*\n\nВыберите обращение:",
        parse_mode="Markdown",
        reply_markup=get_support_tickets_inline_keyboard(answered=False)
    )
    await callback.answer()

@dp.callback_query(F.data == "tickets_answered")
async def show_answered_tickets(callback: CallbackQuery):
    await callback.message.edit_text(
        "📨 *Отвеченные обращения:*\n\nВыберите обращение:",
        parse_mode="Markdown",
        reply_markup=get_support_tickets_inline_keyboard(answered=True)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("tickets_page_"))
async def tickets_pagination(callback: CallbackQuery):
    parts = callback.data.split("_")
    page = int(parts[2])
    answered = parts[3] == "True"
    
    await callback.message.edit_reply_markup(
        reply_markup=get_support_tickets_inline_keyboard(page, answered)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("view_ticket_"))
async def view_ticket(callback: CallbackQuery):
    ticket_id = callback.data.split("_")[2]
    ticket = db.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    
    text = (
        f"📨 *Тикет #{ticket.ticket_id}*\n\n"
        f"👤 *Пользователь:* @{ticket.username} (ID: {ticket.user_id})\n"
        f"📅 *Создан:* {ticket.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📊 *Статус:* {'✅ Отвечен' if ticket.answered else '⏳ Ожидает ответа'}\n\n"
        f"💬 *Сообщение:*\n{ticket.message}\n"
    )
    
    if ticket.replies:
        text += f"\n📝 *История ответов:*\n"
        for reply in ticket.replies:
            text += f"  • {reply['created_at'].strftime('%d.%m.%Y %H:%M')}: {reply['text']}\n"
    
    if ticket.answered:
        text += f"\n👨‍💼 *Ответ:*\n{ticket.answer_message}\n"
        text += f"📅 *Ответ дан:* {ticket.answered_at.strftime('%d.%m.%Y %H:%M')}"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"answer_ticket_{ticket_id}")] if not ticket.answered else [],
            [InlineKeyboardButton(text="📝 Добавить ответ", callback_data=f"reply_ticket_{ticket_id}")] if ticket.answered else [],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_tickets")]
        ]
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("answer_ticket_"))
async def answer_ticket_start(callback: CallbackQuery, state: FSMContext):
    ticket_id = callback.data.split("_")[2]
    ticket = db.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_ticket_answer)
    await state.update_data(ticket_id=ticket_id, user_id=ticket.user_id)
    
    await callback.message.answer(
        f"✏️ Введите ответ на обращение #{ticket_id} для @{ticket.username}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def reply_ticket_start(callback: CallbackQuery, state: FSMContext):
    ticket_id = callback.data.split("_")[2]
    ticket = db.get_ticket(ticket_id)
    
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_ticket_reply)
    await state.update_data(ticket_id=ticket_id, user_id=ticket.user_id)
    
    await callback.message.answer(
        f"✏️ Введите дополнительный ответ для @{ticket.username}:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
        )
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_ticket_answer)
async def process_ticket_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data['ticket_id']
    user_id = data['user_id']
    
    db.answer_ticket(ticket_id, message.from_user.id, message.text)
    
    try:
        await bot.send_message(
            user_id,
            f"📨 *Ответ на ваше обращение #{ticket_id}*\n\n"
            f"{message.text}\n\n"
            f"По всем вопросам вы можете написать снова в поддержку.",
            parse_mode="Markdown"
        )
        
        await message.answer(
            f"✅ Ответ отправлен пользователю!",
            reply_markup=get_admin_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send answer to user {user_id}: {e}")
        await message.answer(
            "❌ Не удалось отправить ответ пользователю. Возможно, пользователь заблокировал бота.",
            reply_markup=get_admin_keyboard()
        )
    
    await state.clear()

@dp.message(AdminStates.waiting_for_ticket_reply)
async def process_ticket_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data['ticket_id']
    user_id = data['user_id']
    
    db.add_ticket_reply(ticket_id, message.from_user.id, message.text)
    
    try:
        await bot.send_message(
            user_id,
            f"📨 *Новый ответ по вашему обращению #{ticket_id}*\n\n"
            f"{message.text}\n\n"
            f"По всем вопросам вы можете написать снова в поддержку.",
            parse_mode="Markdown"
        )
        
        await message.answer(
            f"✅ Дополнительный ответ отправлен пользователю!",
            reply_markup=get_admin_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send reply to user {user_id}: {e}")
        await message.answer(
            "❌ Не удалось отправить ответ пользователю.",
            reply_markup=get_admin_keyboard()
        )
    
    await state.clear()

@dp.callback_query(F.data == "back_to_tickets")
async def back_to_tickets(callback: CallbackQuery):
    await callback.message.edit_text(
        "📨 *Управление обращениями в поддержку*\n\n"
        "Выберите категорию:",
        parse_mode="Markdown",
        reply_markup=get_support_tickets_inline_keyboard()
    )
    await callback.answer()

# ===== Управление заказами (админ) =====

@dp.message(F.text == "📦 Все заказы")
async def admin_all_orders(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    orders = list(db.orders.values())
    orders.sort(key=lambda x: x.created_at, reverse=True)
    
    if not orders:
        await message.answer("📭 Нет заказов.")
        return
    
    for order in orders[:10]:
        status_emoji = {
            OrderStatus.PENDING: "⏳",
            OrderStatus.PAID: "✅",
            OrderStatus.CONFIRMED: "👍",
            OrderStatus.COMPLETED: "🎉",
            OrderStatus.CANCELLED: "❌"
        }.get(order.status, "❓")
        
        status_text = {
            OrderStatus.PENDING: "Ожидает оплаты",
            OrderStatus.PAID: "Оплачено",
            OrderStatus.CONFIRMED: "Подтверждено",
            OrderStatus.COMPLETED: "Завершен",
            OrderStatus.CANCELLED: "Отменен"
        }.get(order.status, order.status.value)
        
        payment_method = "⭐ Звезды" if order.payment_method == PaymentMethod.STARS else "💳 Рубли"
        
        total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        text = (
            f"{status_emoji} *Заказ #{order.order_id}*\n"
            f"👤 @{order.username} (ID: {order.user_id})\n"
            f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"💳 {payment_method}\n"
            f"💰 {total_rub_str}₽ / {total_stars_str}⭐\n"
            f"📊 {status_text}\n"
        )
        
        keyboard = get_order_actions_inline_keyboard(order.order_id, is_admin=True)
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    
    if len(orders) > 10:
        await message.answer(f"Показано 10 из {len(orders)} заказов")

@dp.message(F.text == "⏳ Ожидают оплаты")
async def admin_pending_orders(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    orders = db.get_pending_orders()
    orders.sort(key=lambda x: x.created_at, reverse=True)
    
    if not orders:
        await message.answer("📭 Нет заказов, ожидающих оплаты.")
        return
    
    for order in orders[:10]:
        payment_method = "⭐ Звезды" if order.payment_method == PaymentMethod.STARS else "💳 Рубли"
        
        total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        text = (
            f"⏳ *Заказ #{order.order_id}*\n"
            f"👤 @{order.username} (ID: {order.user_id})\n"
            f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"💳 {payment_method}\n"
            f"💰 {total_rub_str}₽ / {total_stars_str}⭐\n"
            f"📊 Ожидает оплаты\n"
        )
        
        keyboard = get_order_actions_inline_keyboard(order.order_id, is_admin=True)
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    
    if len(orders) > 10:
        await message.answer(f"Показано 10 из {len(orders)} заказов")

@dp.message(F.text == "🔄 Активные заказы")
async def admin_active_orders(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    orders = db.get_active_orders()
    orders.sort(key=lambda x: x.created_at, reverse=True)
    
    if not orders:
        await message.answer("📭 Нет активных заказов.")
        return
    
    for order in orders[:10]:
        status_emoji = "✅" if order.status == OrderStatus.PAID else "👍"
        status_text = "Оплачено" if order.status == OrderStatus.PAID else "Подтверждено"
        payment_method = "⭐ Звезды" if order.payment_method == PaymentMethod.STARS else "💳 Рубли"
        
        total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        text = (
            f"{status_emoji} *Заказ #{order.order_id}*\n"
            f"👤 @{order.username} (ID: {order.user_id})\n"
            f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"💳 {payment_method}\n"
            f"💰 {total_rub_str}₽ / {total_stars_str}⭐\n"
            f"📊 {status_text}\n"
        )
        
        keyboard = get_order_actions_inline_keyboard(order.order_id, is_admin=True)
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    
    if len(orders) > 10:
        await message.answer(f"Показано 10 из {len(orders)} заказов")

@dp.message(F.text == "✅ Завершенные заказы")
async def admin_completed_orders(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    orders = db.get_completed_orders()
    orders.sort(key=lambda x: x.completed_at or x.created_at, reverse=True)
    
    if not orders:
        await message.answer("📭 Нет завершенных заказов.")
        return
    
    for order in orders[:10]:
        payment_method = "⭐ Звезды" if order.payment_method == PaymentMethod.STARS else "💳 Рубли"
        
        total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
        total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
        
        text = (
            f"🎉 *Заказ #{order.order_id}*\n"
            f"👤 @{order.username} (ID: {order.user_id})\n"
            f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"✅ Завершен: {order.completed_at.strftime('%d.%m.%Y %H:%M') if order.completed_at else 'Неизвестно'}\n"
            f"💳 {payment_method}\n"
            f"💰 {total_rub_str}₽ / {total_stars_str}⭐\n"
        )
        
        await message.answer(text, parse_mode="Markdown")
    
    if len(orders) > 10:
        await message.answer(f"Показано 10 из {len(orders)} заказов")

# ===== Действия с заказами (админ) =====

@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_order(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    db.update_order_status(order_id, OrderStatus.CONFIRMED)
    
    total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
    total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
    
    try:
        await bot.send_message(
            order.user_id,
            f"✅ *Заказ #{order_id} подтвержден!*\n\n"
            f"Ваш заказ на сумму {total_rub_str}₽ / {total_stars_str}⭐ подтвержден и передан в обработку.\n"
            f"Ожидайте выполнения.",
            parse_mode="Markdown"
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"✅ Заказ #{order_id} подтвержден!",
        reply_markup=get_order_actions_inline_keyboard(order_id, is_admin=True)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_cancel_"))
async def admin_cancel_order(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    db.update_order_status(order_id, OrderStatus.CANCELLED)
    
    try:
        await bot.send_message(
            order.user_id,
            f"❌ *Заказ #{order_id} отменен*\n\n"
            f"Ваш заказ был отменен администратором.\n"
            f"По вопросам обращайтесь в поддержку.",
            parse_mode="Markdown"
        )
    except:
        pass
    
    await callback.message.edit_text(f"❌ Заказ #{order_id} отменен!")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_complete_"))
async def admin_complete_order(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    db.update_order_status(order_id, OrderStatus.COMPLETED, callback.from_user.id)
    
    total_rub_str = f"{order.total_rub:.2f}".rstrip('0').rstrip('.') if order.total_rub % 1 else str(int(order.total_rub))
    total_stars_str = f"{order.total_stars:.2f}".rstrip('0').rstrip('.') if order.total_stars % 1 else str(int(order.total_stars))
    
    try:
        await bot.send_message(
            order.user_id,
            f"🎉 *Заказ #{order_id} выполнен!*\n\n"
            f"Ваш заказ на сумму {total_rub_str}₽ / {total_stars_str}⭐ успешно выполнен.\n"
            f"Спасибо за покупку!",
            parse_mode="Markdown"
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"🎉 Заказ #{order_id} завершен!",
        reply_markup=get_order_actions_inline_keyboard(order_id, is_admin=True)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("view_screen_"))
async def view_screenshot(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    order_id = callback.data.split("_")[2]
    order = db.get_order(order_id)
    
    if not order or not order.screenshot_file_id:
        await callback.answer("Скриншот не найден", show_alert=True)
        return
    
    await callback.message.answer_photo(
        order.screenshot_file_id,
        caption=f"📸 Скриншот оплаты для заказа #{order_id}"
    )
    await callback.answer()

# ==================== ЗАПУСК БОТА ====================

async def on_startup():
    logger.info("Бот запущен!")
    
    # Добавляем тестовые товары
    db.add_product(
        "Тестовый товар 1",
        "Описание тестового товара 1",
        99.99,
        9.99,
        100
    )
    db.add_product(
        "Тестовый товар 2",
        "Описание тестового товара 2",
        199.50,
        19.50,
        50
    )
    db.add_product(
        "Тестовый товар 3",
        "Описание тестового товара 3",
        49.90,
        4.90,
        200
    )
    
    # Добавляем тестовые промокоды
    db.add_promocode("TEST10", 10, 30, 100)
    db.add_promocode("SALE20", 20, 14, 50)
    
    # Добавляем тестовый конкурс
    db.add_contest(
        "Новогодний розыгрыш",
        "Участвуйте в розыгрыше новогодних призов!",
        "Сертификат на 1000 рублей",
        False,
        None,
        7
    )
    
    bot_info = await bot.get_me()
    db.set_bot_username(bot_info.first_name or "ShopBot")
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "✅ Бот магазина запущен и готов к работе!\n\n"
                "✨ *Новые функции:*\n"
                "• 🎁 Система конкурсов с обязательной покупкой\n"
                "• 🧾 Чеки с именем продавца\n"
                "• 📦 Разделение заказов на активные и завершенные\n"
                "• 🔄 Обновление статуса заказов\n"
                "• 👤 Установка имени продавца\n"
                "• ✏️ Ручной ввод количества товара\n\n"
                "Чтобы настроить группу для отзывов:\n"
                "1. Добавьте бота в группу как администратора\n"
                "2. Отправьте в группе команду /setreviewsgroup",
                parse_mode="Markdown"
            )
        except:
            pass

async def on_shutdown():
    logger.info("Бот остановлен!")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
