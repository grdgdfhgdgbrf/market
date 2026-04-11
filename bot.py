import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import uuid
import random
import json
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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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
    CANCELLED = "cancelled"

class PrizeType(Enum):
    DISCOUNT_PROMOCODE = "discount_promocode"
    FREE_PRODUCT = "free_product"
    CASH_REWARD = "cash_reward"
    STARS_REWARD = "stars_reward"
    CUSTOM_PRIZE = "custom_prize"
    COUPON = "coupon"
    MYSTERY_BOX = "mystery_box"

class BroadcastStatus(Enum):
    DRAFT = "draft"
    SENT = "sent"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"

class BroadcastType(Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"

class RecurringType(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "never"

class Product:
    def __init__(self, id: str, name: str, description: str, price_rub: float, price_stars: float, stock: int = 999):
        self.id = id
        self.name = name
        self.description = description
        self.price_rub = float(price_rub)
        self.price_stars = float(price_stars)
        self.stock = stock
        self.created_at = datetime.now()
        self.purchases_count = 0
        self.revenue_rub = 0.0
        self.revenue_stars = 0.0

class Promotion:
    def __init__(self, code: str, discount_percent: int, valid_until: datetime, 
                 max_uses: int = 1, min_order_amount: float = 0,
                 applicable_products: List[str] = None):
        self.code = code.upper()
        self.discount_percent = discount_percent
        self.valid_until = valid_until
        self.max_uses = max_uses
        self.min_order_amount = min_order_amount
        self.applicable_products = applicable_products or []
        self.used_count = 0
        self.is_active = True

class Prize:
    def __init__(self, prize_type: PrizeType, value: Any, description: str = None):
        self.id = str(uuid.uuid4())[:8]
        self.type = prize_type
        self.value = value
        self.description = description or self._generate_description()
        self.created_at = datetime.now()
        self.claimed = False
        self.claimed_by = None
        self.claimed_at = None

    def _generate_description(self) -> str:
        if self.type == PrizeType.DISCOUNT_PROMOCODE:
            return f"Промокод на скидку {self.value}%"
        elif self.type == PrizeType.FREE_PRODUCT:
            product = db.get_product(self.value) if hasattr(db, 'get_product') else None
            return f"Бесплатный товар: {product.name if product else 'Неизвестный'}"
        elif self.type == PrizeType.CASH_REWARD:
            return f"Денежный приз {self.value}₽"
        elif self.type == PrizeType.STARS_REWARD:
            return f"Приз {self.value}⭐"
        elif self.type == PrizeType.CUSTOM_PRIZE:
            return self.value
        elif self.type == PrizeType.COUPON:
            return f"Купон: {self.value}"
        elif self.type == PrizeType.MYSTERY_BOX:
            return "Секретный приз"
        return "Приз"

class Contest:
    def __init__(self, id: str, name: str, description: str, prizes: List[Prize],
                 required_products: List[str] = None, min_purchase_amount: float = 0,
                 max_participants: int = 0, start_date: datetime = None, 
                 end_date: datetime = None, winners_count: int = 1):
        self.id = id
        self.name = name
        self.description = description
        self.prizes = prizes
        self.required_products = required_products or []
        self.min_purchase_amount = min_purchase_amount
        self.max_participants = max_participants
        self.start_date = start_date or datetime.now()
        self.end_date = end_date or (datetime.now() + timedelta(days=7))
        self.winners_count = winners_count
        self.status = ContestStatus.DRAFT
        self.participants = []
        self.winners = []
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.invite_link = None
        self.broadcast_sent = False
    
    def is_active(self) -> bool:
        return self.status == ContestStatus.ACTIVE and datetime.now() < self.end_date
    
    def can_participate(self) -> bool:
        if self.status != ContestStatus.ACTIVE:
            return False
        if datetime.now() > self.end_date:
            return False
        if self.max_participants > 0 and len(self.participants) >= self.max_participants:
            return False
        return True
    
    def add_participant(self, user_id: int) -> bool:
        if not self.can_participate():
            return False
        if user_id not in self.participants:
            self.participants.append(user_id)
            self.updated_at = datetime.now()
            return True
        return False
    
    def has_participated(self, user_id: int) -> bool:
        return user_id in self.participants
    
    def get_invite_link(self, bot_username: str) -> str:
        if not self.invite_link:
            self.invite_link = f"contest_{self.id}"
        return f"https://t.me/{bot_username}?start={self.invite_link}"
    
    def check_purchase_requirements(self, user_id: int, db_connection) -> Tuple[bool, str]:
        if not self.required_products and self.min_purchase_amount <= 0:
            return True, "Требования отсутствуют"
        
        user_orders = db_connection.get_user_orders(user_id)
        completed_orders = [o for o in user_orders if o.status == OrderStatus.COMPLETED]
        
        total_purchased_amount = 0.0
        purchased_products = set()
        
        for order in completed_orders:
            total_purchased_amount += order.total_rub
            for product_id in order.items:
                purchased_products.add(product_id)
        
        if self.required_products:
            missing_products = []
            for req_product in self.required_products:
                if req_product not in purchased_products:
                    missing_products.append(req_product)
            
            if missing_products:
                product_names = []
                for prod_id in missing_products:
                    product = db_connection.get_product(prod_id)
                    product_names.append(product.name if product else prod_id)
                return False, f"Необходимо приобрести: {', '.join(product_names)}"
        
        if self.min_purchase_amount > 0 and total_purchased_amount < self.min_purchase_amount:
            return False, f"Необходимо совершить покупок на сумму {self.min_purchase_amount:.2f}₽ (у вас: {total_purchased_amount:.2f}₽)"
        
        return True, "Требования выполнены"
    
    def select_winners(self) -> List[Tuple[int, Prize]]:
        if not self.participants:
            return []
        
        winners = []
        available_prizes = self.prizes.copy()
        participants_copy = self.participants.copy()
        
        for i in range(min(self.winners_count, len(participants_copy))):
            if not available_prizes:
                break
            
            participant = random.choice(participants_copy)
            prize = random.choice(available_prizes)
            
            winners.append((participant, prize))
            participants_copy.remove(participant)
            available_prizes.remove(prize)
        
        self.winners = winners
        return winners

class Broadcast:
    def __init__(self, id: str, name: str, message: str, broadcast_type: BroadcastType = BroadcastType.TEXT,
                 media_file_id: str = None, scheduled_time: datetime = None, 
                 target_users: List[int] = None, target_all: bool = True,
                 recurring_id: str = None):
        self.id = id
        self.name = name
        self.message = message
        self.broadcast_type = broadcast_type
        self.media_file_id = media_file_id
        self.scheduled_time = scheduled_time
        self.target_users = target_users or []
        self.target_all = target_all
        self.recurring_id = recurring_id
        self.status = BroadcastStatus.DRAFT
        self.sent_count = 0
        self.failed_count = 0
        self.created_at = datetime.now()
        self.sent_at = None

class BroadcastTemplate:
    def __init__(self, id: str, name: str, template_text: str, 
                 broadcast_type: BroadcastType = BroadcastType.TEXT,
                 media_file_id: str = None):
        self.id = id
        self.name = name
        self.template_text = template_text
        self.broadcast_type = broadcast_type
        self.media_file_id = media_file_id
        self.created_at = datetime.now()

class RecurringBroadcast:
    def __init__(self, id: str, name: str, template_id: str, 
                 recurring_type: RecurringType, next_run: datetime,
                 target_all: bool = True, target_users: List[int] = None):
        self.id = id
        self.name = name
        self.template_id = template_id
        self.recurring_type = recurring_type
        self.next_run = next_run
        self.last_run = None
        self.target_all = target_all
        self.target_users = target_users or []
        self.is_active = True
        self.created_at = datetime.now()

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
        self.broadcasts = {}
        self.broadcast_templates = {}
        self.recurring_broadcasts = {}
        self.generated_coupons = {}
        self.payment_details = {
            "card": "2200 0000 0000 0000",
            "phone": "+7 (999) 999-99-99",
            "bank": "Сбербанк"
        }
        self.next_product_id = 1
        self.next_ticket_id = 1
        self.next_review_id = 1
        self.next_contest_id = 1
        self.next_broadcast_id = 1
        self.next_template_id = 1
        self.next_recurring_id = 1
        self.reviews_group_id = None
        self.bot_username = "ShopBot"
        self.bot_instance = None
        self.stats = {
            'total_users': set(),
            'total_orders': 0,
            'total_revenue_rub': 0.0,
            'total_revenue_stars': 0.0,
            'total_contests': 0,
            'total_participants': 0,
            'total_broadcasts': 0,
            'total_messages_sent': 0
        }

    def set_bot_instance(self, bot_instance):
        self.bot_instance = bot_instance

    def set_bot_username(self, username: str):
        self.bot_username = username

    def get_bot_username(self) -> str:
        return self.bot_username

    # ===== Управление товарами =====
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

    # ===== Корзина =====
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

    # ===== Промокоды =====
    def add_promocode(self, code: str, discount: int, days_valid: int = 7, 
                      max_uses: int = 1, min_order_amount: float = 0,
                      applicable_products: List[str] = None) -> Promotion:
        code = code.upper()
        valid_until = datetime.now() + timedelta(days=days_valid)
        promo = Promotion(code, discount, valid_until, max_uses, min_order_amount, applicable_products)
        self.promocodes[code] = promo
        return promo

    def validate_promocode(self, code: str, cart_total: float = 0, cart_products: List[str] = None) -> Tuple[bool, int, str]:
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
        
        if promo.min_order_amount > 0 and cart_total < promo.min_order_amount:
            return False, 0, f"Минимальная сумма заказа для промокода: {promo.min_order_amount:.2f}₽"
        
        if promo.applicable_products and cart_products:
            applicable = any(p in promo.applicable_products for p in cart_products)
            if not applicable:
                return False, 0, "Промокод не применяется к товарам в корзине"
        
        return True, promo.discount_percent, f"Скидка {promo.discount_percent}%"

    def use_promocode(self, code: str) -> bool:
        code = code.upper()
        if code in self.promocodes:
            self.promocodes[code].used_count += 1
            return True
        return False

    # ===== Заказы =====
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
                product.purchases_count += quantity
                product.revenue_rub += product.price_rub * quantity
                product.revenue_stars += product.price_stars * quantity
        
        if cart.discount > 0:
            total_rub = total_rub * (100 - cart.discount) / 100
            total_stars = total_stars * (100 - cart.discount) / 100
        
        order = Order(order_id, user_id, username, items, total_rub, total_stars, payment_method)
        order.promocode_used = cart.promocode
        
        self.orders[order_id] = order
        self.stats['total_users'].add(user_id)
        self.stats['total_orders'] += 1
        self.stats['total_revenue_rub'] += total_rub
        self.stats['total_revenue_stars'] += total_stars
        
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

    # ===== Поддержка =====
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

    # ===== Отзывы =====
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

    # ===== Конкурсы =====
    def add_contest(self, name: str, description: str, prizes: List[Prize],
                    required_products: List[str] = None, min_purchase_amount: float = 0,
                    max_participants: int = 0, days_valid: int = 7, 
                    winners_count: int = 1) -> Contest:
        contest_id = str(self.next_contest_id)
        self.next_contest_id += 1
        end_date = datetime.now() + timedelta(days=days_valid)
        contest = Contest(contest_id, name, description, prizes,
                         required_products, min_purchase_amount,
                         max_participants, datetime.now(), end_date, winners_count)
        self.contests[contest_id] = contest
        self.stats['total_contests'] += 1
        return contest

    def get_contest(self, contest_id: str) -> Optional[Contest]:
        return self.contests.get(contest_id)

    def get_contest_by_invite(self, invite_link: str) -> Optional[Contest]:
        for contest in self.contests.values():
            if contest.invite_link == invite_link:
                return contest
        return None

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
            contest.updated_at = datetime.now()
            return True
        return False

    def delete_contest(self, contest_id: str) -> bool:
        if contest_id in self.contests:
            del self.contests[contest_id]
            return True
        return False

    def participate_in_contest(self, contest_id: str, user_id: int, via_invite: bool = False) -> Tuple[bool, str]:
        contest = self.get_contest(contest_id)
        if not contest:
            return False, "Конкурс не найден"
        
        if not contest.can_participate():
            return False, "Конкурс недоступен для участия"
        
        if contest.has_participated(user_id):
            return False, "Вы уже участвуете в этом конкурсе"
        
        passed, message = contest.check_purchase_requirements(user_id, self)
        if not passed:
            return False, message
        
        contest.add_participant(user_id)
        self.stats['total_participants'] += 1
        
        if via_invite:
            logger.info(f"User {user_id} joined contest {contest_id} via invite link")
        
        return True, "Вы успешно участвуете в конкурсе! Удачи!"

    def has_participated_in_contest(self, contest_id: str, user_id: int) -> bool:
        contest = self.get_contest(contest_id)
        if contest:
            return contest.has_participated(user_id)
        return False

    def end_contest_and_select_winners(self, contest_id: str) -> List[Tuple[int, Prize]]:
        contest = self.get_contest(contest_id)
        if contest:
            contest.status = ContestStatus.ENDED
            winners = contest.select_winners()
            return winners
        return []

    def get_contest_broadcast_message(self, contest: Contest) -> str:
        message = (
            f"🎁 *НОВЫЙ КОНКУРС!*\n\n"
            f"🏆 *{contest.name}*\n\n"
            f"{contest.description}\n\n"
            f"🎲 *Призы:*\n"
        )
        
        for i, prize in enumerate(contest.prizes, 1):
            message += f"  {i}. {prize.description}\n"
        
        message += f"\n📅 *Окончание:* {contest.end_date.strftime('%d.%m.%Y %H:%M')}\n"
        
        if contest.required_products:
            message += f"📦 *Требуемые товары:*\n"
            for prod_id in contest.required_products:
                product = self.get_product(prod_id)
                if product:
                    message += f"  • {product.name}\n"
        
        if contest.min_purchase_amount > 0:
            message += f"💰 *Мин. сумма покупки:* {contest.min_purchase_amount:.2f}₽\n"
        
        if contest.max_participants > 0:
            message += f"👥 *Макс. участников:* {contest.max_participants}\n"
        
        message += f"\n🎲 *Количество победителей:* {contest.winners_count}\n\n"
        message += f"Участвуйте и выигрывайте призы!\n"
        message += f"🔗 [Участвовать в конкурсе]({contest.get_invite_link(self.bot_username)})"
        
        return message

    # ===== Шаблоны рассылок =====
    def add_broadcast_template(self, name: str, template_text: str, 
                               broadcast_type: BroadcastType = BroadcastType.TEXT,
                               media_file_id: str = None) -> BroadcastTemplate:
        template_id = str(self.next_template_id)
        self.next_template_id += 1
        template = BroadcastTemplate(template_id, name, template_text, broadcast_type, media_file_id)
        self.broadcast_templates[template_id] = template
        return template

    def get_broadcast_template(self, template_id: str) -> Optional[BroadcastTemplate]:
        return self.broadcast_templates.get(template_id)

    def get_all_templates(self) -> List[BroadcastTemplate]:
        return list(self.broadcast_templates.values())

    def delete_broadcast_template(self, template_id: str) -> bool:
        if template_id in self.broadcast_templates:
            del self.broadcast_templates[template_id]
            return True
        return False

    # ===== Регулярные рассылки =====
    def add_recurring_broadcast(self, name: str, template_id: str, 
                                recurring_type: RecurringType, next_run: datetime,
                                target_all: bool = True, target_users: List[int] = None) -> RecurringBroadcast:
        recurring_id = str(self.next_recurring_id)
        self.next_recurring_id += 1
        recurring = RecurringBroadcast(recurring_id, name, template_id, recurring_type, 
                                       next_run, target_all, target_users)
        self.recurring_broadcasts[recurring_id] = recurring
        return recurring

    def get_recurring_broadcast(self, recurring_id: str) -> Optional[RecurringBroadcast]:
        return self.recurring_broadcasts.get(recurring_id)

    def get_all_recurring_broadcasts(self) -> List[RecurringBroadcast]:
        return list(self.recurring_broadcasts.values())

    def delete_recurring_broadcast(self, recurring_id: str) -> bool:
        if recurring_id in self.recurring_broadcasts:
            del self.recurring_broadcasts[recurring_id]
            return True
        return False

    def update_recurring_next_run(self, recurring_id: str):
        recurring = self.get_recurring_broadcast(recurring_id)
        if recurring:
            if recurring.recurring_type == RecurringType.DAILY:
                recurring.next_run += timedelta(days=1)
            elif recurring.recurring_type == RecurringType.WEEKLY:
                recurring.next_run += timedelta(weeks=1)
            elif recurring.recurring_type == RecurringType.MONTHLY:
                new_month = recurring.next_run.month + 1
                new_year = recurring.next_run.year
                if new_month > 12:
                    new_month = 1
                    new_year += 1
                try:
                    recurring.next_run = recurring.next_run.replace(year=new_year, month=new_month)
                except ValueError:
                    recurring.next_run = recurring.next_run.replace(year=new_year, month=new_month, day=28)
            recurring.last_run = datetime.now()

    # ===== Рассылки =====
    def add_broadcast(self, name: str, message: str, broadcast_type: BroadcastType = BroadcastType.TEXT,
                      media_file_id: str = None, scheduled_time: datetime = None,
                      target_users: List[int] = None, target_all: bool = True,
                      recurring_id: str = None) -> Broadcast:
        broadcast_id = str(self.next_broadcast_id)
        self.next_broadcast_id += 1
        broadcast = Broadcast(broadcast_id, name, message, broadcast_type, media_file_id,
                             scheduled_time, target_users, target_all, recurring_id)
        self.broadcasts[broadcast_id] = broadcast
        self.stats['total_broadcasts'] += 1
        return broadcast

    def get_broadcast(self, broadcast_id: str) -> Optional[Broadcast]:
        return self.broadcasts.get(broadcast_id)

    def get_all_broadcasts(self) -> List[Broadcast]:
        return list(self.broadcasts.values())

    def delete_broadcast(self, broadcast_id: str) -> bool:
        if broadcast_id in self.broadcasts:
            del self.broadcasts[broadcast_id]
            return True
        return False

    def get_all_users(self) -> List[int]:
        return list(self.stats['total_users'])

    async def execute_broadcast(self, broadcast_id: str):
        broadcast = self.get_broadcast(broadcast_id)
        if not broadcast or broadcast.status != BroadcastStatus.SCHEDULED:
            return
        
        broadcast.status = BroadcastStatus.SENT
        broadcast.sent_at = datetime.now()
        
        users = self.get_all_users() if broadcast.target_all else broadcast.target_users
        
        for user_id in users:
            if user_id in ADMIN_IDS:
                continue
            
            try:
                if broadcast.broadcast_type == BroadcastType.TEXT:
                    await self.bot_instance.send_message(user_id, broadcast.message, parse_mode="Markdown")
                elif broadcast.broadcast_type == BroadcastType.PHOTO and broadcast.media_file_id:
                    await self.bot_instance.send_photo(user_id, broadcast.media_file_id, caption=broadcast.message, parse_mode="Markdown")
                elif broadcast.broadcast_type == BroadcastType.VIDEO and broadcast.media_file_id:
                    await self.bot_instance.send_video(user_id, broadcast.media_file_id, caption=broadcast.message, parse_mode="Markdown")
                elif broadcast.broadcast_type == BroadcastType.DOCUMENT and broadcast.media_file_id:
                    await self.bot_instance.send_document(user_id, broadcast.media_file_id, caption=broadcast.message, parse_mode="Markdown")
                
                broadcast.sent_count += 1
                self.stats['total_messages_sent'] += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                broadcast.failed_count += 1
                logger.error(f"Broadcast failed for user {user_id}: {e}")

    # ===== Настройки =====
    def update_payment_details(self, card: str = None, phone: str = None, bank: str = None):
        if card:
            self.payment_details["card"] = card
        if phone:
            self.payment_details["phone"] = phone
        if bank:
            self.payment_details["bank"] = bank

    def get_payment_details(self) -> dict:
        return self.payment_details.copy()

    def get_stats(self) -> dict:
        return {
            'total_users': len(self.stats['total_users']),
            'total_orders': self.stats['total_orders'],
            'total_revenue_rub': self.stats['total_revenue_rub'],
            'total_revenue_stars': self.stats['total_revenue_stars'],
            'total_products': len(self.products),
            'total_reviews': len(self.reviews),
            'total_contests': self.stats['total_contests'],
            'total_participants': self.stats['total_participants'],
            'active_contests': len(self.get_active_contests()),
            'pending_orders': len(self.get_pending_orders()),
            'active_orders': len(self.get_active_orders()),
            'completed_orders': len(self.get_completed_orders()),
            'total_broadcasts': self.stats['total_broadcasts'],
            'total_messages_sent': self.stats['total_messages_sent'],
            'total_templates': len(self.broadcast_templates),
            'total_recurring': len(self.recurring_broadcasts)
        }

db = Database()

def format_price(price: float) -> str:
    if price % 1 == 0:
        return str(int(price))
    return f"{price:.2f}".rstrip('0').rstrip('.')

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_group_chat(message: Message) -> bool:
    return message.chat.type in ['group', 'supergroup']

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
    waiting_for_promocode_min_amount = State()
    waiting_for_payment_card = State()
    waiting_for_payment_phone = State()
    waiting_for_payment_bank = State()
    waiting_for_ticket_answer = State()
    waiting_for_ticket_reply = State()
    waiting_for_review_reply = State()
    waiting_for_bot_username = State()
    waiting_for_contest_name = State()
    waiting_for_contest_description = State()
    waiting_for_contest_prize_type = State()
    waiting_for_contest_prize_value = State()
    waiting_for_contest_prize_description = State()
    waiting_for_contest_days = State()
    waiting_for_contest_winners_count = State()
    waiting_for_contest_max_participants = State()
    waiting_for_contest_required_products = State()
    waiting_for_contest_min_amount = State()
    waiting_for_broadcast_name = State()
    waiting_for_broadcast_message = State()
    waiting_for_broadcast_type = State()
    waiting_for_broadcast_media = State()
    waiting_for_broadcast_schedule = State()
    waiting_for_broadcast_target = State()
    waiting_for_template_name = State()
    waiting_for_template_message = State()
    waiting_for_template_type = State()
    waiting_for_template_media = State()
    waiting_for_recurring_name = State()
    waiting_for_recurring_template = State()
    waiting_for_recurring_type = State()
    waiting_for_recurring_time = State()
    waiting_for_recurring_target = State()

class UserStates(StatesGroup):
    waiting_for_promocode = State()
    waiting_for_support_message = State()
    waiting_for_screenshot = State()
    waiting_for_review_rating = State()
    waiting_for_review_comment = State()
    waiting_for_manual_quantity = State()

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
        [KeyboardButton(text="📢 Рассылки"), KeyboardButton(text="👤 Установить имя")],
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
        rub_price = format_price(product.price_rub)
        stars_price = format_price(product.price_stars)
        
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

def get_reviews_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Написать отзыв")],
            [KeyboardButton(text="📖 Все отзывы")],
            [KeyboardButton(text="🔙 На главную")]
        ],
        resize_keyboard=True
    )
    return keyboard

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
        participants_count = len(contest.participants)
        max_participants = contest.max_participants if contest.max_participants > 0 else "∞"
        builder.row(InlineKeyboardButton(
            text=f"🎁 {contest.name} (Участников: {participants_count}/{max_participants})",
            callback_data=f"contest_{contest.id}"
        ))
    
    if not contests:
        builder.row(InlineKeyboardButton(text="📭 Нет активных конкурсов", callback_data="noop"))
    
    builder.row(InlineKeyboardButton(text="◀️ На главную", callback_data="back_to_main"))
    
    return builder.as_markup()

def get_admin_contests_inline_keyboard():
    contests = db.get_all_contests()
    
    builder = InlineKeyboardBuilder()
    
    for contest in contests:
        status_emoji = {
            ContestStatus.DRAFT: "📝",
            ContestStatus.ACTIVE: "🟢",
            ContestStatus.ENDED: "🔴",
            ContestStatus.CANCELLED: "⚫"
        }.get(contest.status, "❓")
        
        builder.row(InlineKeyboardButton(
            text=f"{status_emoji} {contest.name}",
            callback_data=f"admin_contest_{contest.id}"
        ))
    
    builder.row(InlineKeyboardButton(text="➕ Создать конкурс", callback_data="create_contest"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    return builder.as_markup()

def get_prize_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎫 Промокод на скидку", callback_data="prize_type_discount"))
    builder.row(InlineKeyboardButton(text="🎁 Бесплатный товар", callback_data="prize_type_product"))
    builder.row(InlineKeyboardButton(text="💰 Денежный приз", callback_data="prize_type_cash"))
    builder.row(InlineKeyboardButton(text="⭐ Приз звездами", callback_data="prize_type_stars"))
    builder.row(InlineKeyboardButton(text="🎨 Индивидуальный приз", callback_data="prize_type_custom"))
    builder.row(InlineKeyboardButton(text="🎟️ Купон", callback_data="prize_type_coupon"))
    builder.row(InlineKeyboardButton(text="📦 Секретный приз", callback_data="prize_type_mystery"))
    builder.row(InlineKeyboardButton(text="✅ Завершить добавление", callback_data="prize_done"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

def get_broadcast_keyboard():
    buttons = [
        [KeyboardButton(text="➕ Создать рассылку")],
        [KeyboardButton(text="📋 Список рассылок")],
        [KeyboardButton(text="📝 Шаблоны рассылок")],
        [KeyboardButton(text="🔄 Регулярные рассылки")],
        [KeyboardButton(text="📊 Статистика рассылок")],
        [KeyboardButton(text="🔙 На главную")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_broadcast_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📝 Текст", callback_data="broadcast_type_text"))
    builder.row(InlineKeyboardButton(text="🖼️ Фото", callback_data="broadcast_type_photo"))
    builder.row(InlineKeyboardButton(text="🎥 Видео", callback_data="broadcast_type_video"))
    builder.row(InlineKeyboardButton(text="📄 Документ", callback_data="broadcast_type_document"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

def get_broadcast_target_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👥 Всем пользователям", callback_data="broadcast_target_all"))
    builder.row(InlineKeyboardButton(text="🎯 Выбрать вручную", callback_data="broadcast_target_select"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

def get_broadcast_schedule_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚀 Отправить сейчас", callback_data="broadcast_now"))
    builder.row(InlineKeyboardButton(text="⏰ Запланировать", callback_data="broadcast_schedule"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

def get_templates_keyboard():
    templates = db.get_all_templates()
    
    builder = InlineKeyboardBuilder()
    
    for template in templates:
        builder.row(InlineKeyboardButton(
            text=f"📝 {template.name[:30]}",
            callback_data=f"template_{template.id}"
        ))
    
    builder.row(InlineKeyboardButton(text="➕ Создать шаблон", callback_data="create_template"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_broadcasts"))
    
    return builder.as_markup()

def get_recurring_type_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📅 Ежедневно", callback_data="recurring_daily"))
    builder.row(InlineKeyboardButton(text="📆 Еженедельно", callback_data="recurring_weekly"))
    builder.row(InlineKeyboardButton(text="📅 Ежемесячно", callback_data="recurring_monthly"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

def get_recurring_keyboard():
    recurring_list = db.get_all_recurring_broadcasts()
    
    builder = InlineKeyboardBuilder()
    
    for recurring in recurring_list:
        status = "🟢" if recurring.is_active else "🔴"
        type_emoji = {
            RecurringType.DAILY: "📅",
            RecurringType.WEEKLY: "📆",
            RecurringType.MONTHLY: "📅"
        }.get(recurring.recurring_type, "🔄")
        
        builder.row(InlineKeyboardButton(
            text=f"{status} {type_emoji} {recurring.name[:30]}",
            callback_data=f"recurring_{recurring.id}"
        ))
    
    builder.row(InlineKeyboardButton(text="➕ Создать регулярную рассылку", callback_data="create_recurring"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_broadcasts"))
    
    return builder.as_markup()

def get_broadcast_list_keyboard(page: int = 0):
    broadcasts = db.get_all_broadcasts()
    items_per_page = 5
    total_pages = (len(broadcasts) + items_per_page - 1) // items_per_page
    
    start = page * items_per_page
    end = start + items_per_page
    page_broadcasts = broadcasts[start:end]
    
    builder = InlineKeyboardBuilder()
    
    for broadcast in page_broadcasts:
        status_emoji = {
            BroadcastStatus.DRAFT: "📝",
            BroadcastStatus.SENT: "✅",
            BroadcastStatus.SCHEDULED: "⏰",
            BroadcastStatus.CANCELLED: "❌"
        }.get(broadcast.status, "❓")
        
        builder.row(InlineKeyboardButton(
            text=f"{status_emoji} {broadcast.name[:30]}",
            callback_data=f"broadcast_{broadcast.id}"
        ))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"broadcasts_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"broadcasts_page_{page+1}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_broadcasts"))
    
    return builder.as_markup()

def get_cancel_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    return builder.as_markup()

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if is_group_chat(message):
        return
    
    user_id = message.from_user.id
    args = message.text.split()
    
    # Обработка ссылки на конкурс
    if len(args) > 1:
        payload = args[1]
        if payload.startswith("contest_"):
            contest_id = payload.split("_")[1]
            contest = db.get_contest(contest_id)
            
            if contest and contest.is_active():
                success, msg = db.participate_in_contest(contest.id, user_id, via_invite=True)
                
                if success:
                    await message.answer(
                        f"🎉 *Поздравляем!*\n\n"
                        f"Вы успешно участвуете в конкурсе *{contest.name}*!\n\n"
                        f"{msg}\n\n"
                        f"🎲 *Количество участников:* {len(contest.participants)}\n"
                        f"🏆 *Призы:* {len(contest.prizes)} шт.\n\n"
                        f"Желаем удачи!",
                        parse_mode="Markdown",
                        reply_markup=get_main_keyboard(is_admin(user_id))
                    )
                    return
                else:
                    await message.answer(
                        f"❌ *Не удалось участвовать в конкурсе*\n\n{msg}",
                        parse_mode="Markdown",
                        reply_markup=get_main_keyboard(is_admin(user_id))
                    )
                    return
    
    await message.answer(
        f"👋 Добро пожаловать в магазин!\n\n"
        f"Здесь вы можете приобрести товары за звезды или рубли.\n"
        f"Участвуйте в конкурсах и выигрывайте призы!\n\n"
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

# ==================== КОНКУРСЫ (ПОЛЬЗОВАТЕЛИ) ====================

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
    
    passed, requirement_msg = contest.check_purchase_requirements(callback.from_user.id, db)
    has_participated = contest.has_participated(callback.from_user.id)
    
    end_date = contest.end_date.strftime('%d.%m.%Y %H:%M')
    remaining = contest.end_date - datetime.now()
    days = remaining.days
    hours = remaining.seconds // 3600
    
    contest_text = (
        f"🎁 *{contest.name}*\n\n"
        f"{contest.description}\n\n"
        f"🏆 *Призы:*\n"
    )
    
    for i, prize in enumerate(contest.prizes, 1):
        contest_text += f"  {i}. {prize.description}\n"
    
    contest_text += f"\n📅 *Окончание:* {end_date}\n"
    contest_text += f"⏰ *Осталось:* {days} д. {hours} ч.\n"
    contest_text += f"👥 *Участников:* {len(contest.participants)}"
    
    if contest.max_participants > 0:
        contest_text += f"/{contest.max_participants}"
    
    if contest.required_products:
        contest_text += f"\n📦 *Требуемые товары:*\n"
        for prod_id in contest.required_products:
            product = db.get_product(prod_id)
            if product:
                contest_text += f"  • {product.name}\n"
    
    if contest.min_purchase_amount > 0:
        contest_text += f"\n💰 *Минимальная сумма покупки:* {format_price(contest.min_purchase_amount)}₽"
    
    if not passed:
        contest_text += f"\n\n⚠️ *Требования не выполнены:*\n{requirement_msg}"
    elif has_participated:
        contest_text += f"\n\n✅ *Вы уже участвуете в конкурсе!*"
    
    invite_link = contest.get_invite_link(db.get_bot_username())
    contest_text += f"\n\n🔗 *Поделитесь ссылкой с друзьями:*\n`{invite_link}`"
    
    keyboard = InlineKeyboardBuilder()
    
    if passed and not has_participated:
        keyboard.row(InlineKeyboardButton(text="🎲 Участвовать", callback_data=f"participate_{contest_id}"))
    
    keyboard.row(InlineKeyboardButton(text="📤 Поделиться ссылкой", callback_data=f"share_contest_{contest_id}"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад к конкурсам", callback_data="back_to_contests"))
    
    try:
        await callback.message.edit_text(
            contest_text,
            parse_mode="Markdown",
            reply_markup=keyboard.as_markup()
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            contest_text,
            parse_mode="Markdown",
            reply_markup=keyboard.as_markup()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("participate_"))
async def participate_in_contest(callback: CallbackQuery):
    contest_id = callback.data.split("_")[1]
    
    success, message = db.participate_in_contest(contest_id, callback.from_user.id, via_invite=False)
    
    if success:
        await callback.answer("✅ Вы успешно участвуете в конкурсе! Удачи!", show_alert=True)
        await view_contest(callback)
    else:
        await callback.answer(f"❌ {message}", show_alert=True)

@dp.callback_query(F.data.startswith("share_contest_"))
async def share_contest(callback: CallbackQuery):
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if not contest:
        await callback.answer("Конкурс не найден", show_alert=True)
        return
    
    invite_link = contest.get_invite_link(db.get_bot_username())
    
    share_text = (
        f"🎁 *Приглашаю участвовать в конкурсе!*\n\n"
        f"🏆 *{contest.name}*\n\n"
        f"{contest.description}\n\n"
        f"🎲 *Призы:* {len(contest.prizes)} шт.\n"
        f"👥 *Участников:* {len(contest.participants)}\n\n"
        f"Переходи по ссылке и участвуй!\n"
        f"{invite_link}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=share_text)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"contest_{contest_id}")]
        ]
    )
    
    await callback.message.answer(
        "🔗 *Поделитесь ссылкой на конкурс:*\n\n"
        f"`{invite_link}`\n\n"
        f"Нажмите на кнопку ниже, чтобы поделиться:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()

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

# ==================== УПРАВЛЕНИЕ КОНКУРСАМИ (АДМИН) ====================

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

# Класс для временного хранения данных конкурса
class ContestCreationData:
    def __init__(self):
        self.name = ""
        self.description = ""
        self.prizes = []
        self.days = 7
        self.winners_count = 1
        self.max_participants = 0
        self.required_products = []
        self.min_purchase_amount = 0.0
        self.current_prize_type = None
        self.current_prize_value = None

@dp.callback_query(F.data == "create_contest")
async def create_contest_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_contest_name)
    await state.update_data(contest_creation=ContestCreationData())
    await callback.message.answer(
        "Введите название конкурса:",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_contest_name)
async def create_contest_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    if creation:
        creation.name = message.text
        await state.update_data(contest_creation=creation)
    
    await state.set_state(AdminStates.waiting_for_contest_description)
    await message.answer(
        "Введите описание конкурса:",
        reply_markup=get_cancel_inline_keyboard()
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
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    if creation:
        creation.description = message.text
        await state.update_data(contest_creation=creation)
    
    await state.set_state(AdminStates.waiting_for_contest_prize_type)
    await message.answer(
        "🎁 *Добавление призов*\n\n"
        "Выберите тип приза:",
        parse_mode="Markdown",
        reply_markup=get_prize_type_keyboard()
    )

@dp.callback_query(F.data.startswith("prize_type_"))
async def add_prize_type(callback: CallbackQuery, state: FSMContext):
    prize_type_str = callback.data.split("_")[2]
    
    prize_type_map = {
        "discount": PrizeType.DISCOUNT_PROMOCODE,
        "product": PrizeType.FREE_PRODUCT,
        "cash": PrizeType.CASH_REWARD,
        "stars": PrizeType.STARS_REWARD,
        "custom": PrizeType.CUSTOM_PRIZE,
        "coupon": PrizeType.COUPON,
        "mystery": PrizeType.MYSTERY_BOX
    }
    
    prize_type = prize_type_map.get(prize_type_str)
    if not prize_type:
        await callback.answer("Неизвестный тип приза")
        return
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    if creation:
        creation.current_prize_type = prize_type
        await state.update_data(contest_creation=creation)
    
    if prize_type == PrizeType.DISCOUNT_PROMOCODE:
        await state.set_state(AdminStates.waiting_for_contest_prize_value)
        await callback.message.answer(
            "Введите процент скидки для промокода (1-100):",
            reply_markup=get_cancel_inline_keyboard()
        )
    elif prize_type == PrizeType.FREE_PRODUCT:
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
                text=f"{product.name} - {format_price(product.price_rub)}₽",
                callback_data=f"select_product_prize_{product.id}"
            ))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
        
        await callback.message.edit_text(
            "Выберите товар для приза:",
            reply_markup=builder.as_markup()
        )
    elif prize_type in [PrizeType.CASH_REWARD, PrizeType.STARS_REWARD]:
        await state.set_state(AdminStates.waiting_for_contest_prize_value)
        unit = "₽" if prize_type == PrizeType.CASH_REWARD else "⭐"
        await callback.message.answer(
            f"Введите сумму приза в {unit}:",
            reply_markup=get_cancel_inline_keyboard()
        )
    else:
        await state.set_state(AdminStates.waiting_for_contest_prize_description)
        await callback.message.answer(
            "Введите описание приза:",
            reply_markup=get_cancel_inline_keyboard()
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("select_product_prize_"))
async def select_product_prize(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split("_")[3]
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    
    if creation:
        prize = Prize(PrizeType.FREE_PRODUCT, product_id, None)
        creation.prizes.append(prize)
        creation.current_prize_type = None
        
        await state.update_data(contest_creation=creation)
        
        await callback.message.answer(
            f"✅ Приз добавлен!\n\n"
            f"Текущие призы: {len(creation.prizes)}\n\n"
            f"Выберите следующий тип приза или завершите добавление:",
            reply_markup=get_prize_type_keyboard()
        )
    await callback.answer()

@dp.message(AdminStates.waiting_for_contest_prize_value)
async def create_contest_prize_value(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    
    if not creation:
        await message.answer("❌ Ошибка создания конкурса")
        await state.clear()
        return
    
    try:
        if creation.current_prize_type == PrizeType.DISCOUNT_PROMOCODE:
            value = int(message.text)
            if value < 1 or value > 100:
                raise ValueError
        elif creation.current_prize_type in [PrizeType.CASH_REWARD, PrizeType.STARS_REWARD]:
            value = float(message.text.replace(',', '.'))
            if value <= 0:
                raise ValueError
        else:
            value = message.text
        
        prize = Prize(creation.current_prize_type, value, None)
        creation.prizes.append(prize)
        creation.current_prize_type = None
        
        await state.update_data(contest_creation=creation)
        
        await message.answer(
            f"✅ Приз добавлен!\n\n"
            f"Текущие призы: {len(creation.prizes)}\n\n"
            f"Выберите следующий тип приза или завершите добавление:",
            reply_markup=get_prize_type_keyboard()
        )
    except ValueError:
        await message.answer(
            "❌ Неверное значение. Попробуйте снова:",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.message(AdminStates.waiting_for_contest_prize_description)
async def create_contest_prize_description(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    
    if not creation:
        await message.answer("❌ Ошибка создания конкурса")
        await state.clear()
        return
    
    prize = Prize(creation.current_prize_type, message.text, message.text)
    creation.prizes.append(prize)
    creation.current_prize_type = None
    
    await state.update_data(contest_creation=creation)
    
    await message.answer(
        f"✅ Приз добавлен!\n\n"
        f"Текущие призы: {len(creation.prizes)}\n\n"
        f"Выберите следующий тип приза или завершите добавление:",
        reply_markup=get_prize_type_keyboard()
    )

@dp.callback_query(F.data == "prize_done")
async def finish_adding_prizes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    creation = data.get('contest_creation')
    
    if not creation or not creation.prizes:
        await callback.answer("Добавьте хотя бы один приз!", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_for_contest_days)
    await callback.message.answer(
        "Введите количество дней действия конкурса (по умолчанию 7):\n\n"
        "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏩ Пропустить (7 дней)", callback_data="skip_days")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "skip_days")
async def skip_days(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_contest_winners_count)
    await callback.message.answer(
        "Введите количество победителей (по умолчанию 1):\n\n"
        "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏩ Пропустить (1 победитель)", callback_data="skip_winners")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
    )
    await callback.answer()

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
        
        data = await state.get_data()
        creation = data.get('contest_creation')
        if creation:
            creation.days = days
            await state.update_data(contest_creation=creation)
        
        await state.set_state(AdminStates.waiting_for_contest_winners_count)
        await message.answer(
            "Введите количество победителей (по умолчанию 1):\n\n"
            "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⏩ Пропустить (1 победитель)", callback_data="skip_winners")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
                ]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число дней",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data == "skip_winners")
async def skip_winners(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_contest_max_participants)
    await callback.message.answer(
        "Введите максимальное количество участников (0 - без ограничений):\n\n"
        "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏩ Пропустить (без ограничений)", callback_data="skip_max_participants")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_contest_winners_count)
async def create_contest_winners_count(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        winners_count = int(message.text)
        if winners_count < 1:
            raise ValueError
        
        data = await state.get_data()
        creation = data.get('contest_creation')
        if creation:
            creation.winners_count = winners_count
            await state.update_data(contest_creation=creation)
        
        await state.set_state(AdminStates.waiting_for_contest_max_participants)
        await message.answer(
            "Введите максимальное количество участников (0 - без ограничений):\n\n"
            "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⏩ Пропустить (без ограничений)", callback_data="skip_max_participants")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
                ]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data == "skip_max_participants")
async def skip_max_participants(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_contest_required_products)
    
    products = db.get_all_products()
    if products:
        builder = InlineKeyboardBuilder()
        for product in products:
            builder.row(InlineKeyboardButton(
                text=f"📦 {product.name} - {format_price(product.price_rub)}₽",
                callback_data=f"add_required_product_{product.id}"
            ))
        builder.row(InlineKeyboardButton(text="✅ Завершить выбор", callback_data="finish_required_products"))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
        
        await callback.message.answer(
            "Выберите товары, которые необходимо купить для участия в конкурсе\n"
            "(можно выбрать несколько, нажимая на кнопки):\n\n"
            "После выбора нажмите 'Завершить выбор'.",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.message.answer(
            "Нет доступных товаров для выбора обязательных.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Продолжить без требований", callback_data="no_required_products")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
                ]
            )
        )
    await callback.answer()

@dp.message(AdminStates.waiting_for_contest_max_participants)
async def create_contest_max_participants(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        max_participants = int(message.text)
        if max_participants < 0:
            raise ValueError
        
        data = await state.get_data()
        creation = data.get('contest_creation')
        if creation:
            creation.max_participants = max_participants
            await state.update_data(contest_creation=creation)
        
        await state.set_state(AdminStates.waiting_for_contest_required_products)
        
        products = db.get_all_products()
        if products:
            builder = InlineKeyboardBuilder()
            for product in products:
                builder.row(InlineKeyboardButton(
                    text=f"📦 {product.name} - {format_price(product.price_rub)}₽",
                    callback_data=f"add_required_product_{product.id}"
                ))
            builder.row(InlineKeyboardButton(text="✅ Завершить выбор", callback_data="finish_required_products"))
            builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
            
            await message.answer(
                "Выберите товары, которые необходимо купить для участия в конкурсе\n"
                "(можно выбрать несколько, нажимая на кнопки):\n\n"
                "После выбора нажмите 'Завершить выбор'.",
                reply_markup=builder.as_markup()
            )
        else:
            await message.answer(
                "Нет доступных товаров для выбора обязательных.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Продолжить без требований", callback_data="no_required_products")],
                        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
                    ]
                )
            )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data.startswith("add_required_product_"))
async def add_required_product(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split("_")[3]
    
    data = await state.get_data()
    creation = data.get('contest_creation')
    
    if creation:
        if product_id not in creation.required_products:
            creation.required_products.append(product_id)
            await state.update_data(contest_creation=creation)
            
            product = db.get_product(product_id)
            await callback.answer(f"✅ {product.name} добавлен в обязательные товары")
        else:
            await callback.answer("Этот товар уже добавлен")
    
    products = db.get_all_products()
    builder = InlineKeyboardBuilder()
    for product in products:
        status = "✅ " if creation and product.id in creation.required_products else "📦 "
        builder.row(InlineKeyboardButton(
            text=f"{status}{product.name} - {format_price(product.price_rub)}₽",
            callback_data=f"add_required_product_{product.id}"
        ))
    builder.row(InlineKeyboardButton(text="✅ Завершить выбор", callback_data="finish_required_products"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "finish_required_products")
async def finish_required_products(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_contest_min_amount)
    await callback.message.answer(
        "Введите минимальную сумму покупки для участия в конкурсе (0 - без ограничений):\n\n"
        "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏩ Пропустить (без ограничений)", callback_data="skip_min_amount_contest")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "no_required_products")
async def no_required_products(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_contest_min_amount)
    await callback.message.answer(
        "Введите минимальную сумму покупки для участия в конкурсе (0 - без ограничений):\n\n"
        "Отправьте число или нажмите 'Пропустить' для значения по умолчанию.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏩ Пропустить (без ограничений)", callback_data="skip_min_amount_contest")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "skip_min_amount_contest")
async def skip_min_amount_contest(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    creation = data.get('contest_creation')
    
    if not creation:
        await callback.answer("Ошибка создания конкурса", show_alert=True)
        return
    
    contest = db.add_contest(
        name=creation.name,
        description=creation.description,
        prizes=creation.prizes,
        required_products=creation.required_products,
        min_purchase_amount=creation.min_purchase_amount,
        max_participants=creation.max_participants,
        days_valid=creation.days,
        winners_count=creation.winners_count
    )
    
    contest.status = ContestStatus.ACTIVE
    
    # Создаем ссылку для конкурса
    contest.invite_link = f"contest_{contest.id}"
    
    # Отправляем авто-рассылку о новом конкурсе
    broadcast_message = db.get_contest_broadcast_message(contest)
    broadcast = db.add_broadcast(
        name=f"Новый конкурс: {contest.name}",
        message=broadcast_message,
        broadcast_type=BroadcastType.TEXT,
        target_all=True
    )
    broadcast.status = BroadcastStatus.SCHEDULED
    broadcast.scheduled_time = datetime.now()
    asyncio.create_task(db.execute_broadcast(broadcast.id))
    
    await callback.message.edit_text(
        f"✅ *Конкурс создан и активирован!*\n\n"
        f"🎁 Название: {contest.name}\n"
        f"🏆 Призов: {len(contest.prizes)}\n"
        f"👥 Макс. участников: {contest.max_participants if contest.max_participants > 0 else 'Без ограничений'}\n"
        f"🎲 Победителей: {contest.winners_count}\n"
        f"📦 Обязательные товары: {len(contest.required_products)}\n"
        f"💰 Мин. сумма покупки: {format_price(contest.min_purchase_amount) if contest.min_purchase_amount > 0 else 'Без ограничений'}₽\n"
        f"📅 Длительность: {creation.days} дней\n\n"
        f"🔗 *Ссылка для участия:*\n`{contest.get_invite_link(db.get_bot_username())}`\n\n"
        f"📢 *Автоматическая рассылка о конкурсе отправлена пользователям!*\n\n"
        f"Конкурс доступен для участия!",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()
    await callback.answer()

@dp.message(AdminStates.waiting_for_contest_min_amount)
async def create_contest_min_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        min_amount = float(message.text.replace(',', '.'))
        if min_amount < 0:
            raise ValueError
        
        data = await state.get_data()
        creation = data.get('contest_creation')
        
        if not creation:
            await message.answer("❌ Ошибка создания конкурса")
            await state.clear()
            return
        
        creation.min_purchase_amount = min_amount
        await state.update_data(contest_creation=creation)
        
        contest = db.add_contest(
            name=creation.name,
            description=creation.description,
            prizes=creation.prizes,
            required_products=creation.required_products,
            min_purchase_amount=min_amount,
            max_participants=creation.max_participants,
            days_valid=creation.days,
            winners_count=creation.winners_count
        )
        
        contest.status = ContestStatus.ACTIVE
        
        # Создаем ссылку для конкурса
        contest.invite_link = f"contest_{contest.id}"
        
        # Отправляем авто-рассылку о новом конкурсе
        broadcast_message = db.get_contest_broadcast_message(contest)
        broadcast = db.add_broadcast(
            name=f"Новый конкурс: {contest.name}",
            message=broadcast_message,
            broadcast_type=BroadcastType.TEXT,
            target_all=True
        )
        broadcast.status = BroadcastStatus.SCHEDULED
        broadcast.scheduled_time = datetime.now()
        asyncio.create_task(db.execute_broadcast(broadcast.id))
        
        await message.answer(
            f"✅ *Конкурс создан и активирован!*\n\n"
            f"🎁 Название: {contest.name}\n"
            f"🏆 Призов: {len(contest.prizes)}\n"
            f"👥 Макс. участников: {contest.max_participants if contest.max_participants > 0 else 'Без ограничений'}\n"
            f"🎲 Победителей: {contest.winners_count}\n"
            f"📦 Обязательные товары: {len(contest.required_products)}\n"
            f"💰 Мин. сумма покупки: {format_price(min_amount) if min_amount > 0 else 'Без ограничений'}₽\n"
            f"📅 Длительность: {creation.days} дней\n\n"
            f"🔗 *Ссылка для участия:*\n`{contest.get_invite_link(db.get_bot_username())}`\n\n"
            f"📢 *Автоматическая рассылка о конкурсе отправлена пользователям!*\n\n"
            f"Конкурс доступен для участия!",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Введите корректное число",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data.startswith("admin_contest_"))
async def admin_view_contest(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if not contest:
        await callback.answer("Конкурс не найден", show_alert=True)
        return
    
    status_emoji = {
        ContestStatus.DRAFT: "📝",
        ContestStatus.ACTIVE: "🟢",
        ContestStatus.ENDED: "🔴",
        ContestStatus.CANCELLED: "⚫"
    }.get(contest.status, "❓")
    
    status_text = {
        ContestStatus.DRAFT: "Черновик",
        ContestStatus.ACTIVE: "Активен",
        ContestStatus.ENDED: "Завершен",
        ContestStatus.CANCELLED: "Отменен"
    }.get(contest.status, "Неизвестно")
    
    text = (
        f"{status_emoji} *{contest.name}*\n\n"
        f"📝 {contest.description}\n\n"
        f"🏆 *Призы:*\n"
    )
    
    for i, prize in enumerate(contest.prizes, 1):
        text += f"  {i}. {prize.description}\n"
    
    text += f"\n📊 *Статус:* {status_text}\n"
    text += f"📅 *Создан:* {contest.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    text += f"📅 *Окончание:* {contest.end_date.strftime('%d.%m.%Y %H:%M')}\n"
    text += f"👥 *Участников:* {len(contest.participants)}"
    
    if contest.max_participants > 0:
        text += f"/{contest.max_participants}"
    
    text += f"\n🎲 *Победителей:* {contest.winners_count}\n"
    
    if contest.required_products:
        text += f"\n📦 *Обязательные товары:*\n"
        for prod_id in contest.required_products:
            product = db.get_product(prod_id)
            if product:
                text += f"  • {product.name}\n"
    
    if contest.min_purchase_amount > 0:
        text += f"\n💰 *Мин. сумма покупки:* {format_price(contest.min_purchase_amount)}₽"
    
    invite_link = contest.get_invite_link(db.get_bot_username())
    text += f"\n\n🔗 *Ссылка для участия:*\n`{invite_link}`"
    
    if contest.winners:
        text += f"\n\n🏆 *Победители:*\n"
        for winner_id, prize in contest.winners:
            try:
                user = await bot.get_chat(winner_id)
                username = user.username or str(winner_id)
                text += f"  • @{username} - {prize.description}\n"
            except:
                text += f"  • ID:{winner_id} - {prize.description}\n"
    
    keyboard_buttons = []
    
    if contest.status == ContestStatus.DRAFT:
        keyboard_buttons.append([InlineKeyboardButton(text="✅ Активировать", callback_data=f"activate_contest_{contest_id}")])
    elif contest.status == ContestStatus.ACTIVE:
        keyboard_buttons.append([InlineKeyboardButton(text="⏹️ Завершить досрочно", callback_data=f"end_contest_{contest_id}")])
    
    keyboard_buttons.append([InlineKeyboardButton(text="🎲 Выбрать победителей", callback_data=f"select_winners_{contest_id}")])
    keyboard_buttons.append([InlineKeyboardButton(text="📤 Скопировать ссылку", callback_data=f"copy_contest_link_{contest_id}")])
    keyboard_buttons.append([InlineKeyboardButton(text="🗑 Удалить конкурс", callback_data=f"delete_contest_{contest_id}")])
    keyboard_buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_contests_admin")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("copy_contest_link_"))
async def copy_contest_link(callback: CallbackQuery):
    contest_id = callback.data.split("_")[3]
    contest = db.get_contest(contest_id)
    
    if contest:
        invite_link = contest.get_invite_link(db.get_bot_username())
        
        await callback.message.answer(
            f"🔗 *Ссылка для участия в конкурсе \"{contest.name}\":*\n\n"
            f"`{invite_link}`\n\n"
            f"Нажмите на ссылку, чтобы скопировать.",
            parse_mode="Markdown"
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("activate_contest_"))
async def activate_contest(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if contest:
        contest.status = ContestStatus.ACTIVE
        
        # Отправляем авто-рассылку о новом конкурсе
        broadcast_message = db.get_contest_broadcast_message(contest)
        broadcast = db.add_broadcast(
            name=f"Новый конкурс: {contest.name}",
            message=broadcast_message,
            broadcast_type=BroadcastType.TEXT,
            target_all=True
        )
        broadcast.status = BroadcastStatus.SCHEDULED
        broadcast.scheduled_time = datetime.now()
        asyncio.create_task(db.execute_broadcast(broadcast.id))
        
        await callback.message.edit_text(
            f"✅ Конкурс \"{contest.name}\" активирован!\n\n"
            f"🔗 *Ссылка для участия:*\n`{contest.get_invite_link(db.get_bot_username())}`\n\n"
            f"📢 Автоматическая рассылка отправлена пользователям!"
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("end_contest_"))
async def end_contest_early(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if contest:
        contest.status = ContestStatus.ENDED
        await callback.message.edit_text(f"⏹️ Конкурс \"{contest.name}\" завершен досрочно!")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("select_winners_"))
async def select_winners(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    
    contest_id = callback.data.split("_")[2]
    contest = db.get_contest(contest_id)
    
    if not contest:
        await callback.answer("Конкурс не найден", show_alert=True)
        return
    
    if not contest.participants:
        await callback.answer("Нет участников для выбора победителей!", show_alert=True)
        return
    
    winners = contest.select_winners()
    
    text = f"🏆 *Победители конкурса \"{contest.name}\":*\n\n"
    
    for winner_id, prize in winners:
        try:
            user = await bot.get_chat(winner_id)
            username = user.username or str(winner_id)
            text += f"• @{username} - {prize.description}\n"
            
            await bot.send_message(
                winner_id,
                f"🎉 *Поздравляем! Вы выиграли в конкурсе \"{contest.name}\"!*\n\n"
                f"Ваш приз: {prize.description}\n\n"
                f"Для получения приза обратитесь к администратору.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify winner {winner_id}: {e}")
            text += f"• Пользователь ID:{winner_id} - {prize.description} (не удалось уведомить)\n"
    
    for participant in contest.participants:
        if participant not in [w[0] for w in winners]:
            try:
                await bot.send_message(
                    participant,
                    f"🏆 *Конкурс \"{contest.name}\" завершен!*\n\n"
                    f"К сожалению, вы не стали победителем в этот раз.\n"
                    f"Следите за новыми конкурсами и удачи в следующий раз!",
                    parse_mode="Markdown"
                )
            except:
                pass
    
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_contest_"))
async def delete_contest(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
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

# ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (КАТАЛОГ, КОРЗИНА, ОПЛАТА и т.д.) ====================

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
        rub_price = format_price(item['price_rub'])
        stars_price = format_price(item['price_stars'])
        item_total_rub = format_price(item['total_rub'])
        item_total_stars = format_price(item['total_stars'])
        
        cart_text += f"• *{item['name']}* x{item['quantity']}\n"
        cart_text += f"  {rub_price}₽ x{item['quantity']} = {item_total_rub}₽\n"
        cart_text += f"  {stars_price}⭐ x{item['quantity']} = {item_total_stars}⭐\n\n"
    
    if cart.discount > 0:
        original_rub_str = format_price(original_rub)
        original_stars_str = format_price(original_stars)
        total_rub_str = format_price(total_rub)
        total_stars_str = format_price(total_stars)
        
        cart_text += f"*Скидка по промокоду* `{cart.promocode}`: {cart.discount}%\n"
        cart_text += f"*Было:* {original_rub_str}₽ / {original_stars_str}⭐\n"
        cart_text += f"*Стало:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    else:
        total_rub_str = format_price(total_rub)
        total_stars_str = format_price(total_stars)
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
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Активные заказы")],
            [KeyboardButton(text="✅ Завершенные заказы")],
            [KeyboardButton(text="🔙 На главную")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        "📦 *Ваши заказы*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=keyboard
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
        
        total_rub_str = format_price(order.total_rub)
        total_stars_str = format_price(order.total_stars)
        
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
        
        total_rub_str = format_price(order.total_rub)
        total_stars_str = format_price(order.total_stars)
        
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
    cart = db.get_cart(message.from_user.id)
    
    cart_total = 0.0
    cart_products = []
    for product_id, quantity in cart.items.items():
        product = db.get_product(product_id)
        if product:
            cart_total += product.price_rub * quantity
            cart_products.append(product_id)
    
    valid, discount, msg = db.validate_promocode(code, cart_total, cart_products)
    
    if valid:
        cart.promocode = code
        cart.discount = discount
        db.use_promocode(code)
        
        await message.answer(
            f"✅ {msg}\n\n"
            f"Скидка {discount}% применена к вашей корзине!",
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
            rub_price = format_price(product.price_rub)
            stars_price = format_price(product.price_stars)
            receipt_text += f"• {product.name} x{quantity}\n"
            receipt_text += f"  {rub_price}₽ x{quantity} = {format_price(product.price_rub * quantity)}₽\n"
            receipt_text += f"  {stars_price}⭐ x{quantity} = {format_price(product.price_stars * quantity)}⭐\n"
    
    receipt_text += "\n" + "=" * 30 + "\n"
    
    if order.promocode_used:
        receipt_text += f"🏷 *Промокод:* {order.promocode_used}\n"
    
    total_rub_str = format_price(order.total_rub)
    total_stars_str = format_price(order.total_stars)
    
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
    
    rub_price = format_price(product.price_rub)
    stars_price = format_price(product.price_stars)
    
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
    
    rub_price = format_price(product.price_rub)
    stars_price = format_price(product.price_stars)
    
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
    
    rub_price = format_price(product.price_rub)
    stars_price = format_price(product.price_stars)
    
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
        rub_price = format_price(product.price_rub)
        stars_price = format_price(product.price_stars)
        
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
        
        rub_price = format_price(product.price_rub)
        stars_price = format_price(product.price_stars)
        
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
        rub_price = format_price(item['price_rub'])
        stars_price = format_price(item['price_stars'])
        item_total_rub = format_price(item['total_rub'])
        item_total_stars = format_price(item['total_stars'])
        
        cart_text += f"• {item['name']} x{item['quantity']}\n"
        cart_text += f"  {rub_price}₽ x{item['quantity']} = {item_total_rub}₽\n"
        cart_text += f"  {stars_price}⭐ x{item['quantity']} = {item_total_stars}⭐\n\n"
    
    if cart.discount > 0:
        original_rub_str = format_price(original_rub)
        original_stars_str = format_price(original_stars)
        total_rub_str = format_price(total_rub)
        total_stars_str = format_price(total_stars)
        
        cart_text += f"*Скидка:* {cart.discount}%\n"
        cart_text += f"*Итого со скидкой:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    else:
        total_rub_str = format_price(total_rub)
        total_stars_str = format_price(total_stars)
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
        rub_price = format_price(item['price_rub'])
        stars_price = format_price(item['price_stars'])
        item_total_rub = format_price(item['total_rub'])
        item_total_stars = format_price(item['total_stars'])
        
        cart_text += f"• {item['name']} x{item['quantity']}\n"
        cart_text += f"  {rub_price}₽ x{item['quantity']} = {item_total_rub}₽\n"
        cart_text += f"  {stars_price}⭐ x{item['quantity']} = {item_total_stars}⭐\n\n"
    
    if cart.discount > 0:
        original_rub_str = format_price(original_rub)
        original_stars_str = format_price(original_stars)
        total_rub_str = format_price(total_rub)
        total_stars_str = format_price(total_stars)
        
        cart_text += f"*Скидка:* {cart.discount}%\n"
        cart_text += f"*Итого со скидкой:* {total_rub_str}₽ / {total_stars_str}⭐\n"
    else:
        total_rub_str = format_price(total_rub)
        total_stars_str = format_price(total_stars)
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
    
    total_rub_str = format_price(order.total_rub)
    
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
    
    total_rub_str = format_price(order.total_rub)
    
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
        
        total_stars_str = format_price(order.total_stars)
        
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

@dp.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

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

# ==================== АДМИН-ПАНЕЛЬ ====================

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

@dp.message(F.text == "👤 Установить имя")
async def set_bot_username_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_bot_username)
    await message.answer(
        "Введите имя продавца/магазина, которое будет отображаться в чеках:",
        reply_markup=get_cancel_inline_keyboard()
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
        reply_markup=get_cancel_inline_keyboard()
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

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_product_name)
    await message.answer(
        "Введите название товара:",
        reply_markup=get_cancel_inline_keyboard()
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
        reply_markup=get_cancel_inline_keyboard()
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
        reply_markup=get_cancel_inline_keyboard()
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
            reply_markup=get_cancel_inline_keyboard()
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число (можно дробное)",
            reply_markup=get_cancel_inline_keyboard()
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
            reply_markup=get_cancel_inline_keyboard()
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число (можно дробное)",
            reply_markup=get_cancel_inline_keyboard()
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
        
        rub_price = format_price(product.price_rub)
        stars_price = format_price(product.price_stars)
        
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
            reply_markup=get_cancel_inline_keyboard()
        )

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
        reply_markup=get_cancel_inline_keyboard()
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
        
        rub_price = format_price(product.price_rub)
        stars_price = format_price(product.price_stars)
        
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
            reply_markup=get_cancel_inline_keyboard()
        )

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

@dp.message(F.text == "🎫 Создать промокод")
async def create_promocode_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_promocode_code)
    await message.answer(
        "Введите код промокода:",
        reply_markup=get_cancel_inline_keyboard()
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
        reply_markup=get_cancel_inline_keyboard()
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
            reply_markup=get_cancel_inline_keyboard()
        )
    except ValueError:
        await message.answer(
            "❌ Введите число от 1 до 100",
            reply_markup=get_cancel_inline_keyboard()
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
            reply_markup=get_cancel_inline_keyboard()
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число дней",
            reply_markup=get_cancel_inline_keyboard()
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
        
        await state.update_data(promo_uses=uses)
        await state.set_state(AdminStates.waiting_for_promocode_min_amount)
        await message.answer(
            "Введите минимальную сумму заказа для применения промокода (0 - без ограничений):",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⏩ Пропустить", callback_data="skip_min_amount_promo")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
                ]
            )
        )
    except ValueError:
        await message.answer(
            "❌ Введите корректное число",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data == "skip_min_amount_promo")
async def skip_min_amount_promo(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    promo = db.add_promocode(
        data['promo_code'],
        data['promo_discount'],
        data['promo_days'],
        data['promo_uses']
    )
    
    await callback.message.edit_text(
        f"✅ Промокод создан!\n\n"
        f"Код: `{promo.code}`\n"
        f"Скидка: {promo.discount_percent}%\n"
        f"Действует: {promo.valid_until.strftime('%d.%m.%Y')}\n"
        f"Использований: {promo.max_uses}",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )
    await state.clear()
    await callback.answer()

@dp.message(AdminStates.waiting_for_promocode_min_amount)
async def create_promocode_min_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        min_amount = float(message.text.replace(',', '.'))
        if min_amount < 0:
            raise ValueError
        
        data = await state.get_data()
        promo = db.add_promocode(
            data['promo_code'],
            data['promo_discount'],
            data['promo_days'],
            data['promo_uses'],
            min_amount
        )
        
        await message.answer(
            f"✅ Промокод создан!\n\n"
            f"Код: `{promo.code}`\n"
            f"Скидка: {promo.discount_percent}%\n"
            f"Мин. сумма: {format_price(min_amount)}₽\n"
            f"Действует: {promo.valid_until.strftime('%d.%m.%Y')}\n"
            f"Использований: {promo.max_uses}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Введите корректное число",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.message(F.text == "💳 Реквизиты оплаты")
async def payment_details_menu(message: Message):
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
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "edit_phone")
async def edit_phone(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_payment_phone)
    await callback.message.answer(
        "Введите номер телефона:",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "edit_bank")
async def edit_bank(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_payment_bank)
    await callback.message.answer(
        "Введите название банка:",
        reply_markup=get_cancel_inline_keyboard()
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

@dp.message(F.text == "📊 Статистика")
async def show_statistics(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    stats = db.get_stats()
    
    text = (
        f"📊 *Статистика магазина*\n\n"
        f"👥 *Пользователи:* {stats['total_users']}\n"
        f"📦 *Заказов:* {stats['total_orders']}\n"
        f"  • Ожидают оплаты: {stats['pending_orders']}\n"
        f"  • В обработке: {stats['active_orders']}\n"
        f"  • Завершено: {stats['completed_orders']}\n\n"
        f"💰 *Выручка:*\n"
        f"  • Рубли: {format_price(stats['total_revenue_rub'])}₽\n"
        f"  • Звезды: {format_price(stats['total_revenue_stars'])}⭐\n\n"
        f"🛍 *Товаров:* {stats['total_products']}\n"
        f"⭐ *Отзывов:* {stats['total_reviews']}\n"
        f"🎁 *Конкурсов:*\n"
        f"  • Всего: {stats['total_contests']}\n"
        f"  • Активных: {stats['active_contests']}\n"
        f"  • Участников: {stats['total_participants']}\n\n"
        f"📢 *Рассылки:*\n"
        f"  • Всего: {stats['total_broadcasts']}\n"
        f"  • Шаблонов: {stats['total_templates']}\n"
        f"  • Регулярных: {stats['total_recurring']}\n"
        f"  • Сообщений отправлено: {stats['total_messages_sent']}"
    )
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

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
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer(
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
        reply_markup=get_cancel_inline_keyboard()
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
        reply_markup=get_cancel_inline_keyboard()
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
        
        total_rub_str = format_price(order.total_rub)
        total_stars_str = format_price(order.total_stars)
        
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
        
        total_rub_str = format_price(order.total_rub)
        total_stars_str = format_price(order.total_stars)
        
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
        
        total_rub_str = format_price(order.total_rub)
        total_stars_str = format_price(order.total_stars)
        
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
        
        total_rub_str = format_price(order.total_rub)
        total_stars_str = format_price(order.total_stars)
        
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
    
    total_rub_str = format_price(order.total_rub)
    total_stars_str = format_price(order.total_stars)
    
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
    
    total_rub_str = format_price(order.total_rub)
    total_stars_str = format_price(order.total_stars)
    
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

# ==================== РАССЫЛКИ ====================

@dp.message(F.text == "📢 Рассылки")
async def broadcasts_menu(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await message.answer(
        "📢 *Управление рассылками*\n\n"
        "Здесь вы можете создавать и управлять рассылками для пользователей.\n\n"
        "• 📝 *Создать рассылку* - разовая рассылка\n"
        "• 📋 *Список рассылок* - просмотр всех рассылок\n"
        "• 📝 *Шаблоны рассылок* - создание и использование шаблонов\n"
        "• 🔄 *Регулярные рассылки* - ежедневные/еженедельные рассылки\n"
        "• 📊 *Статистика* - общая статистика рассылок",
        parse_mode="Markdown",
        reply_markup=get_broadcast_keyboard()
    )

@dp.message(F.text == "➕ Создать рассылку")
async def create_broadcast_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await state.set_state(AdminStates.waiting_for_broadcast_name)
    await message.answer(
        "Введите название рассылки:",
        reply_markup=get_cancel_inline_keyboard()
    )

@dp.message(AdminStates.waiting_for_broadcast_name)
async def create_broadcast_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(broadcast_name=message.text)
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await message.answer(
        "Введите текст сообщения для рассылки (можно использовать Markdown):\n\n"
        "Доступные переменные:\n"
        "• {name} - имя пользователя\n"
        "• {username} - username пользователя\n"
        "• {date} - текущая дата\n"
        "• {time} - текущее время",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📝 Использовать шаблон", callback_data="use_template")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
            ]
        )
    )

@dp.callback_query(F.data == "use_template")
async def use_template_for_broadcast(callback: CallbackQuery, state: FSMContext):
    templates = db.get_all_templates()
    
    if not templates:
        await callback.answer("Нет доступных шаблонов", show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()
    for template in templates:
        builder.row(InlineKeyboardButton(
            text=f"📝 {template.name}",
            callback_data=f"select_template_{template.id}"
        ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    
    await callback.message.answer(
        "Выберите шаблон:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("select_template_"))
async def apply_template(callback: CallbackQuery, state: FSMContext):
    template_id = callback.data.split("_")[2]
    template = db.get_broadcast_template(template_id)
    
    if template:
        await state.update_data(broadcast_message=template.template_text)
        await state.update_data(broadcast_type=template.broadcast_type)
        await state.update_data(broadcast_media=template.media_file_id)
        
        await callback.message.answer(
            f"✅ Шаблон \"{template.name}\" применен!\n\n"
            f"Текст сообщения:\n{template.template_text[:200]}...\n\n"
            f"Продолжаем настройку рассылки...",
            reply_markup=get_broadcast_schedule_keyboard()
        )
        await state.set_state(AdminStates.waiting_for_broadcast_schedule)
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_message)
async def create_broadcast_message(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(broadcast_message=message.text)
    await state.set_state(AdminStates.waiting_for_broadcast_type)
    await message.answer(
        "Выберите тип рассылки:",
        reply_markup=get_broadcast_type_keyboard()
    )

@dp.callback_query(F.data.startswith("broadcast_type_"))
async def create_broadcast_type(callback: CallbackQuery, state: FSMContext):
    broadcast_type_str = callback.data.split("_")[2]
    
    type_map = {
        "text": BroadcastType.TEXT,
        "photo": BroadcastType.PHOTO,
        "video": BroadcastType.VIDEO,
        "document": BroadcastType.DOCUMENT
    }
    
    broadcast_type = type_map.get(broadcast_type_str, BroadcastType.TEXT)
    await state.update_data(broadcast_type=broadcast_type)
    
    if broadcast_type != BroadcastType.TEXT:
        await state.set_state(AdminStates.waiting_for_broadcast_media)
        await callback.message.answer(
            "Отправьте медиафайл для рассылки (фото, видео или документ):",
            reply_markup=get_cancel_inline_keyboard()
        )
    else:
        await state.set_state(AdminStates.waiting_for_broadcast_schedule)
        await callback.message.answer(
            "Выберите время отправки:",
            reply_markup=get_broadcast_schedule_keyboard()
        )
    
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_media)
async def create_broadcast_media(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id
    else:
        await message.answer(
            "❌ Пожалуйста, отправьте фото, видео или документ.",
            reply_markup=get_cancel_inline_keyboard()
        )
        return
    
    await state.update_data(broadcast_media=file_id)
    await state.set_state(AdminStates.waiting_for_broadcast_schedule)
    await message.answer(
        "Выберите время отправки:",
        reply_markup=get_broadcast_schedule_keyboard()
    )

@dp.callback_query(F.data == "broadcast_now")
async def broadcast_now(callback: CallbackQuery, state: FSMContext):
    await state.update_data(broadcast_schedule=None)
    await state.set_state(AdminStates.waiting_for_broadcast_target)
    await callback.message.answer(
        "Выберите аудиторию для рассылки:",
        reply_markup=get_broadcast_target_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "broadcast_schedule")
async def broadcast_schedule(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Введите дату и время отправки в формате: `DD.MM.YYYY HH:MM`\n\n"
        "Например: `25.12.2024 15:30`",
        parse_mode="Markdown",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_schedule)
async def process_broadcast_schedule(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        schedule_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        if schedule_time < datetime.now():
            await message.answer(
                "❌ Время должно быть в будущем. Попробуйте снова:",
                reply_markup=get_cancel_inline_keyboard()
            )
            return
        
        await state.update_data(broadcast_schedule=schedule_time)
        await state.set_state(AdminStates.waiting_for_broadcast_target)
        await message.answer(
            "Выберите аудиторию для рассылки:",
            reply_markup=get_broadcast_target_keyboard()
        )
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Используйте: `DD.MM.YYYY HH:MM`\n\nНапример: `25.12.2024 15:30`",
            parse_mode="Markdown",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data == "broadcast_target_all")
async def broadcast_target_all(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    broadcast = db.add_broadcast(
        name=data['broadcast_name'],
        message=data['broadcast_message'],
        broadcast_type=data['broadcast_type'],
        media_file_id=data.get('broadcast_media'),
        scheduled_time=data.get('broadcast_schedule'),
        target_all=True
    )
    
    if data.get('broadcast_schedule'):
        broadcast.status = BroadcastStatus.SCHEDULED
        await callback.message.answer(
            f"✅ Рассылка \"{broadcast.name}\" запланирована на {data['broadcast_schedule'].strftime('%d.%m.%Y %H:%M')}!",
            reply_markup=get_admin_keyboard()
        )
    else:
        broadcast.status = BroadcastStatus.SCHEDULED
        broadcast.scheduled_time = datetime.now()
        
        await callback.message.answer(
            f"✅ Рассылка \"{broadcast.name}\" запущена!",
            reply_markup=get_admin_keyboard()
        )
        
        asyncio.create_task(db.execute_broadcast(broadcast.id))
    
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "broadcast_target_select")
async def broadcast_target_select(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Введите ID пользователей через запятую (например: 123456789, 987654321):",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_target)
async def process_broadcast_target(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        user_ids = [int(x.strip()) for x in message.text.split(',')]
        data = await state.get_data()
        
        broadcast = db.add_broadcast(
            name=data['broadcast_name'],
            message=data['broadcast_message'],
            broadcast_type=data['broadcast_type'],
            media_file_id=data.get('broadcast_media'),
            scheduled_time=data.get('broadcast_schedule'),
            target_all=False,
            target_users=user_ids
        )
        
        if data.get('broadcast_schedule'):
            broadcast.status = BroadcastStatus.SCHEDULED
            await message.answer(
                f"✅ Рассылка \"{broadcast.name}\" запланирована на {data['broadcast_schedule'].strftime('%d.%m.%Y %H:%M')}!\n"
                f"Получателей: {len(user_ids)}",
                reply_markup=get_admin_keyboard()
            )
        else:
            broadcast.status = BroadcastStatus.SCHEDULED
            broadcast.scheduled_time = datetime.now()
            
            await message.answer(
                f"✅ Рассылка \"{broadcast.name}\" запущена!\n"
                f"Получателей: {len(user_ids)}",
                reply_markup=get_admin_keyboard()
            )
            
            asyncio.create_task(db.execute_broadcast(broadcast.id))
        
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Введите ID через запятую.",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.message(F.text == "📋 Список рассылок")
async def list_broadcasts(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    broadcasts = db.get_all_broadcasts()
    
    if not broadcasts:
        await message.answer("📭 Нет созданных рассылок.")
        return
    
    await message.answer(
        "📋 *Список рассылок*\n\nВыберите рассылку для просмотра:",
        parse_mode="Markdown",
        reply_markup=get_broadcast_list_keyboard()
    )

@dp.callback_query(F.data.startswith("broadcasts_page_"))
async def broadcasts_pagination(callback: CallbackQuery):
    page = int(callback.data.split("_")[2])
    await callback.message.edit_reply_markup(reply_markup=get_broadcast_list_keyboard(page))
    await callback.answer()

@dp.callback_query(F.data.startswith("broadcast_"))
async def view_broadcast(callback: CallbackQuery):
    broadcast_id = callback.data.split("_")[1]
    broadcast = db.get_broadcast(broadcast_id)
    
    if not broadcast:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return
    
    status_text = {
        BroadcastStatus.DRAFT: "📝 Черновик",
        BroadcastStatus.SENT: "✅ Отправлена",
        BroadcastStatus.SCHEDULED: "⏰ Запланирована",
        BroadcastStatus.CANCELLED: "❌ Отменена"
    }.get(broadcast.status, "❓")
    
    text = (
        f"📢 *{broadcast.name}*\n\n"
        f"📊 *Статус:* {status_text}\n"
        f"📝 *Тип:* {broadcast.broadcast_type.value}\n"
        f"👥 *Аудитория:* {'Все пользователи' if broadcast.target_all else f'{len(broadcast.target_users)} пользователей'}\n"
        f"✅ *Доставлено:* {broadcast.sent_count}\n"
        f"❌ *Ошибок:* {broadcast.failed_count}\n"
        f"📅 *Создана:* {broadcast.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    )
    
    if broadcast.scheduled_time:
        text += f"⏰ *Запланирована:* {broadcast.scheduled_time.strftime('%d.%m.%Y %H:%M')}\n"
    
    if broadcast.sent_at:
        text += f"📅 *Отправлена:* {broadcast.sent_at.strftime('%d.%m.%Y %H:%M')}\n"
    
    text += f"\n💬 *Сообщение:*\n{broadcast.message[:500]}"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_broadcast_{broadcast_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_broadcasts")]
        ]
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_broadcast_"))
async def delete_broadcast(callback: CallbackQuery):
    broadcast_id = callback.data.split("_")[2]
    broadcast = db.get_broadcast(broadcast_id)
    
    if broadcast:
        db.delete_broadcast(broadcast_id)
        await callback.message.edit_text(f"✅ Рассылка \"{broadcast.name}\" удалена!")
    
    await callback.answer()

@dp.message(F.text == "📝 Шаблоны рассылок")
async def templates_menu(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await message.answer(
        "📝 *Шаблоны рассылок*\n\n"
        "Здесь вы можете создавать и использовать шаблоны для рассылок.\n\n"
        "Шаблоны позволяют быстро создавать типовые рассылки.",
        parse_mode="Markdown",
        reply_markup=get_templates_keyboard()
    )

@dp.callback_query(F.data == "create_template")
async def create_template_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_template_name)
    await callback.message.answer(
        "Введите название шаблона:",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_template_name)
async def create_template_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(template_name=message.text)
    await state.set_state(AdminStates.waiting_for_template_message)
    await message.answer(
        "Введите текст шаблона (можно использовать Markdown):\n\n"
        "Доступные переменные:\n"
        "• {name} - имя пользователя\n"
        "• {username} - username пользователя\n"
        "• {date} - текущая дата\n"
        "• {time} - текущее время",
        reply_markup=get_cancel_inline_keyboard()
    )

@dp.message(AdminStates.waiting_for_template_message)
async def create_template_message(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(template_message=message.text)
    await state.set_state(AdminStates.waiting_for_template_type)
    await message.answer(
        "Выберите тип шаблона:",
        reply_markup=get_broadcast_type_keyboard()
    )

@dp.callback_query(F.data.startswith("broadcast_type_"))
async def create_template_type(callback: CallbackQuery, state: FSMContext):
    broadcast_type_str = callback.data.split("_")[2]
    
    type_map = {
        "text": BroadcastType.TEXT,
        "photo": BroadcastType.PHOTO,
        "video": BroadcastType.VIDEO,
        "document": BroadcastType.DOCUMENT
    }
    
    broadcast_type = type_map.get(broadcast_type_str, BroadcastType.TEXT)
    await state.update_data(template_type=broadcast_type)
    
    if broadcast_type != BroadcastType.TEXT:
        await state.set_state(AdminStates.waiting_for_template_media)
        await callback.message.answer(
            "Отправьте медиафайл для шаблона (фото, видео или документ):",
            reply_markup=get_cancel_inline_keyboard()
        )
    else:
        data = await state.get_data()
        template = db.add_broadcast_template(
            name=data['template_name'],
            template_text=data['template_message'],
            broadcast_type=broadcast_type
        )
        
        await callback.message.answer(
            f"✅ Шаблон \"{template.name}\" успешно создан!\n\n"
            f"ID: {template.id}\n"
            f"Тип: {template.broadcast_type.value}",
            reply_markup=get_admin_keyboard()
        )
        await state.clear()
    
    await callback.answer()

@dp.message(AdminStates.waiting_for_template_media)
async def create_template_media(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id
    else:
        await message.answer(
            "❌ Пожалуйста, отправьте фото, видео или документ.",
            reply_markup=get_cancel_inline_keyboard()
        )
        return
    
    data = await state.get_data()
    template = db.add_broadcast_template(
        name=data['template_name'],
        template_text=data['template_message'],
        broadcast_type=data['template_type'],
        media_file_id=file_id
    )
    
    await message.answer(
        f"✅ Шаблон \"{template.name}\" успешно создан!\n\n"
        f"ID: {template.id}\n"
        f"Тип: {template.broadcast_type.value}",
        reply_markup=get_admin_keyboard()
    )
    await state.clear()

@dp.callback_query(F.data.startswith("template_"))
async def view_template(callback: CallbackQuery):
    template_id = callback.data.split("_")[1]
    template = db.get_broadcast_template(template_id)
    
    if not template:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    
    text = (
        f"📝 *Шаблон: {template.name}*\n\n"
        f"📊 *Тип:* {template.broadcast_type.value}\n"
        f"📅 *Создан:* {template.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"💬 *Текст:*\n{template.template_text[:500]}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Использовать", callback_data=f"use_template_{template_id}")],
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_template_{template_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_templates")]
        ]
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("use_template_"))
async def use_template_from_menu(callback: CallbackQuery, state: FSMContext):
    template_id = callback.data.split("_")[2]
    template = db.get_broadcast_template(template_id)
    
    if template:
        await state.update_data(broadcast_name=template.name)
        await state.update_data(broadcast_message=template.template_text)
        await state.update_data(broadcast_type=template.broadcast_type)
        await state.update_data(broadcast_media=template.media_file_id)
        
        await state.set_state(AdminStates.waiting_for_broadcast_schedule)
        await callback.message.answer(
            f"✅ Шаблон \"{template.name}\" загружен!\n\n"
            f"Теперь выберите время отправки:",
            reply_markup=get_broadcast_schedule_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_template_"))
async def delete_template(callback: CallbackQuery):
    template_id = callback.data.split("_")[2]
    template = db.get_broadcast_template(template_id)
    
    if template:
        db.delete_broadcast_template(template_id)
        await callback.message.edit_text(f"✅ Шаблон \"{template.name}\" удален!")
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_templates")
async def back_to_templates(callback: CallbackQuery):
    await callback.message.edit_text(
        "📝 *Шаблоны рассылок*\n\n"
        "Здесь вы можете создавать и использовать шаблоны для рассылок.",
        parse_mode="Markdown",
        reply_markup=get_templates_keyboard()
    )
    await callback.answer()

@dp.message(F.text == "🔄 Регулярные рассылки")
async def recurring_broadcasts_menu(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    await message.answer(
        "🔄 *Регулярные рассылки*\n\n"
        "Здесь вы можете настроить автоматические регулярные рассылки.\n\n"
        "• Ежедневные - каждый день в указанное время\n"
        "• Еженедельные - раз в неделю\n"
        "• Ежемесячные - раз в месяц",
        parse_mode="Markdown",
        reply_markup=get_recurring_keyboard()
    )

@dp.callback_query(F.data == "create_recurring")
async def create_recurring_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_recurring_name)
    await callback.message.answer(
        "Введите название регулярной рассылки:",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_recurring_name)
async def create_recurring_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await state.update_data(recurring_name=message.text)
    await state.set_state(AdminStates.waiting_for_recurring_template)
    
    templates = db.get_all_templates()
    if not templates:
        await message.answer(
            "❌ Нет доступных шаблонов. Сначала создайте шаблон рассылки.",
            reply_markup=get_admin_keyboard()
        )
        await state.clear()
        return
    
    builder = InlineKeyboardBuilder()
    for template in templates:
        builder.row(InlineKeyboardButton(
            text=f"📝 {template.name}",
            callback_data=f"select_recurring_template_{template.id}"
        ))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    
    await message.answer(
        "Выберите шаблон для регулярной рассылки:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("select_recurring_template_"))
async def select_recurring_template(callback: CallbackQuery, state: FSMContext):
    template_id = callback.data.split("_")[3]
    template = db.get_broadcast_template(template_id)
    
    if template:
        await state.update_data(recurring_template_id=template_id)
        await state.set_state(AdminStates.waiting_for_recurring_type)
        await callback.message.answer(
            "Выберите периодичность рассылки:",
            reply_markup=get_recurring_type_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("recurring_"))
async def select_recurring_type(callback: CallbackQuery, state: FSMContext):
    recurring_type_str = callback.data.split("_")[1]
    
    type_map = {
        "daily": RecurringType.DAILY,
        "weekly": RecurringType.WEEKLY,
        "monthly": RecurringType.MONTHLY
    }
    
    recurring_type = type_map.get(recurring_type_str, RecurringType.DAILY)
    await state.update_data(recurring_type=recurring_type)
    await state.set_state(AdminStates.waiting_for_recurring_time)
    
    await callback.message.answer(
        "Введите время отправки в формате `HH:MM` (например: `15:30`):\n\n"
        "Рассылка будет отправляться каждый день в указанное время.",
        parse_mode="Markdown",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_recurring_time)
async def create_recurring_time(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        time_parts = message.text.split(':')
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError
        
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if next_run <= now:
            next_run += timedelta(days=1)
        
        await state.update_data(recurring_next_run=next_run)
        await state.set_state(AdminStates.waiting_for_recurring_target)
        await message.answer(
            "Выберите аудиторию для рассылки:",
            reply_markup=get_broadcast_target_keyboard()
        )
    except (ValueError, IndexError):
        await message.answer(
            "❌ Неверный формат. Используйте `HH:MM` (например: `15:30`):",
            parse_mode="Markdown",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data == "broadcast_target_all")
async def recurring_target_all(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    recurring = db.add_recurring_broadcast(
        name=data['recurring_name'],
        template_id=data['recurring_template_id'],
        recurring_type=data['recurring_type'],
        next_run=data['recurring_next_run'],
        target_all=True
    )
    
    await callback.message.answer(
        f"✅ Регулярная рассылка \"{recurring.name}\" создана!\n\n"
        f"📅 *Периодичность:* {recurring.recurring_type.value}\n"
        f"⏰ *Следующий запуск:* {recurring.next_run.strftime('%d.%m.%Y %H:%M')}\n"
        f"👥 *Аудитория:* Все пользователи\n\n"
        f"Рассылка будет автоматически отправляться по расписанию.",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "broadcast_target_select")
async def recurring_target_select(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Введите ID пользователей через запятую (например: 123456789, 987654321):",
        reply_markup=get_cancel_inline_keyboard()
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_recurring_target)
async def process_recurring_target(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
        return
    
    try:
        user_ids = [int(x.strip()) for x in message.text.split(',')]
        data = await state.get_data()
        
        recurring = db.add_recurring_broadcast(
            name=data['recurring_name'],
            template_id=data['recurring_template_id'],
            recurring_type=data['recurring_type'],
            next_run=data['recurring_next_run'],
            target_all=False,
            target_users=user_ids
        )
        
        await message.answer(
            f"✅ Регулярная рассылка \"{recurring.name}\" создана!\n\n"
            f"📅 *Периодичность:* {recurring.recurring_type.value}\n"
            f"⏰ *Следующий запуск:* {recurring.next_run.strftime('%d.%m.%Y %H:%M')}\n"
            f"👥 *Аудитория:* {len(user_ids)} пользователей\n\n"
            f"Рассылка будет автоматически отправляться по расписанию.",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Введите ID через запятую.",
            reply_markup=get_cancel_inline_keyboard()
        )

@dp.callback_query(F.data.startswith("recurring_"))
async def view_recurring(callback: CallbackQuery):
    recurring_id = callback.data.split("_")[1]
    recurring = db.get_recurring_broadcast(recurring_id)
    
    if not recurring:
        await callback.answer("Регулярная рассылка не найдена", show_alert=True)
        return
    
    template = db.get_broadcast_template(recurring.template_id)
    template_name = template.name if template else "Неизвестный шаблон"
    
    status = "🟢 Активна" if recurring.is_active else "🔴 Отключена"
    type_text = {
        RecurringType.DAILY: "Ежедневно",
        RecurringType.WEEKLY: "Еженедельно",
        RecurringType.MONTHLY: "Ежемесячно"
    }.get(recurring.recurring_type, "Неизвестно")
    
    text = (
        f"🔄 *Регулярная рассылка: {recurring.name}*\n\n"
        f"📊 *Статус:* {status}\n"
        f"📝 *Шаблон:* {template_name}\n"
        f"🕐 *Периодичность:* {type_text}\n"
        f"⏰ *Следующий запуск:* {recurring.next_run.strftime('%d.%m.%Y %H:%M')}\n"
    )
    
    if recurring.last_run:
        text += f"📅 *Последний запуск:* {recurring.last_run.strftime('%d.%m.%Y %H:%M')}\n"
    
    text += f"👥 *Аудитория:* {'Все пользователи' if recurring.target_all else f'{len(recurring.target_users)} пользователей'}\n"
    text += f"📅 *Создана:* {recurring.created_at.strftime('%d.%m.%Y %H:%M')}"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏸️ Остановить" if recurring.is_active else "▶️ Запустить", 
                                 callback_data=f"toggle_recurring_{recurring_id}")],
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_recurring_{recurring_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_recurring")]
        ]
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("toggle_recurring_"))
async def toggle_recurring(callback: CallbackQuery):
    recurring_id = callback.data.split("_")[2]
    recurring = db.get_recurring_broadcast(recurring_id)
    
    if recurring:
        recurring.is_active = not recurring.is_active
        status = "запущена" if recurring.is_active else "остановлена"
        await callback.message.edit_text(f"✅ Регулярная рассылка \"{recurring.name}\" {status}!")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_recurring_"))
async def delete_recurring(callback: CallbackQuery):
    recurring_id = callback.data.split("_")[2]
    recurring = db.get_recurring_broadcast(recurring_id)
    
    if recurring:
        db.delete_recurring_broadcast(recurring_id)
        await callback.message.edit_text(f"✅ Регулярная рассылка \"{recurring.name}\" удалена!")
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_recurring")
async def back_to_recurring(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔄 *Регулярные рассылки*\n\n"
        "Здесь вы можете настроить автоматические регулярные рассылки.",
        parse_mode="Markdown",
        reply_markup=get_recurring_keyboard()
    )
    await callback.answer()

@dp.message(F.text == "📊 Статистика рассылок")
async def broadcast_stats(message: Message):
    if not is_admin(message.from_user.id) or is_group_chat(message):
        return
    
    broadcasts = db.get_all_broadcasts()
    total_sent = sum(b.sent_count for b in broadcasts)
    total_failed = sum(b.failed_count for b in broadcasts)
    templates_count = len(db.get_all_templates())
    recurring_count = len(db.get_all_recurring_broadcasts())
    
    text = (
        f"📊 *Статистика рассылок*\n\n"
        f"📢 *Всего рассылок:* {len(broadcasts)}\n"
        f"✅ *Доставлено сообщений:* {total_sent}\n"
        f"❌ *Ошибок:* {total_failed}\n"
        f"📈 *Успешность:* {(total_sent/(total_sent+total_failed)*100) if total_sent+total_failed > 0 else 0:.1f}%\n\n"
        f"📝 *Шаблонов:* {templates_count}\n"
        f"🔄 *Регулярных рассылок:* {recurring_count}\n\n"
        f"📋 *По статусам:*\n"
        f"  • Черновики: {len([b for b in broadcasts if b.status == BroadcastStatus.DRAFT])}\n"
        f"  • Отправлены: {len([b for b in broadcasts if b.status == BroadcastStatus.SENT])}\n"
        f"  • Запланированы: {len([b for b in broadcasts if b.status == BroadcastStatus.SCHEDULED])}\n"
        f"  • Отменены: {len([b for b in broadcasts if b.status == BroadcastStatus.CANCELLED])}"
    )
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_broadcast_keyboard())

@dp.callback_query(F.data == "back_to_broadcasts")
async def back_to_broadcasts(callback: CallbackQuery):
    await callback.message.edit_text(
        "📢 *Управление рассылками*\n\n"
        "Здесь вы можете создавать и управлять рассылками для пользователей.",
        parse_mode="Markdown",
        reply_markup=get_broadcast_keyboard()
    )
    await callback.answer()

# ==================== ЗАПУСК БОТА ====================

async def check_recurring_broadcasts():
    """Фоновая задача для проверки и отправки регулярных рассылок"""
    while True:
        try:
            now = datetime.now()
            recurring_list = db.get_all_recurring_broadcasts()
            
            for recurring in recurring_list:
                if recurring.is_active and recurring.next_run <= now:
                    template = db.get_broadcast_template(recurring.template_id)
                    
                    if template:
                        broadcast = db.add_broadcast(
                            name=f"Регулярная: {recurring.name} - {now.strftime('%d.%m.%Y %H:%M')}",
                            message=template.template_text,
                            broadcast_type=template.broadcast_type,
                            media_file_id=template.media_file_id,
                            scheduled_time=now,
                            target_all=recurring.target_all,
                            target_users=recurring.target_users,
                            recurring_id=recurring.id
                        )
                        broadcast.status = BroadcastStatus.SCHEDULED
                        asyncio.create_task(db.execute_broadcast(broadcast.id))
                        
                        db.update_recurring_next_run(recurring.id)
            
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Error in recurring broadcasts checker: {e}")
            await asyncio.sleep(60)

async def on_startup():
    logger.info("Бот запущен!")
    
    db.set_bot_instance(bot)
    
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
    db.add_promocode("SALE20", 20, 14, 50, 500)
    
    # Добавляем тестовые шаблоны
    db.add_broadcast_template(
        name="Приветственный",
        template_text="👋 Добро пожаловать в наш магазин!\n\nРады видеть вас среди наших покупателей!"
    )
    db.add_broadcast_template(
        name="Новости магазина",
        template_text="📢 *Новости магазина*\n\nУ нас новые поступления!\n\nПриходите посмотреть!"
    )
    
    # Добавляем тестовый конкурс
    prizes = [
        Prize(PrizeType.DISCOUNT_PROMOCODE, 50, "Промокод на скидку 50%"),
        Prize(PrizeType.CASH_REWARD, 1000, "Денежный приз 1000₽"),
        Prize(PrizeType.STARS_REWARD, 500, "500⭐ на счет")
    ]
    
    contest = db.add_contest(
        name="Новогодний розыгрыш",
        description="Участвуйте в розыгрыше новогодних призов!",
        prizes=prizes,
        required_products=[],
        min_purchase_amount=0,
        max_participants=0,
        days_valid=7,
        winners_count=3
    )
    contest.status = ContestStatus.ACTIVE
    contest.invite_link = f"contest_{contest.id}"
    
    bot_info = await bot.get_me()
    db.set_bot_username(bot_info.username or "ShopBot")
    
    asyncio.create_task(check_recurring_broadcasts())
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "✅ *Бот магазина запущен и готов к работе!*\n\n"
                "✨ *Основные функции:*\n"
                "• 🛍 Каталог товаров с оплатой звездами/рублями\n"
                "• 🛒 Корзина и оформление заказов\n"
                "• 📦 Отслеживание заказов\n"
                "• ⭐ Отзывы о покупках\n"
                "• 🎁 Конкурсы с различными типами призов\n"
                "• 🔗 Ссылки для приглашения в конкурсы (работают через /start)\n"
                "• 📢 Рассылки (шаблоны, регулярные, авто-рассылки)\n"
                "• 🎫 Промокоды с настройками\n"
                "• 📊 Полная статистика\n\n"
                "📌 *Как работают ссылки на конкурсы:*\n"
                "• При создании конкурса генерируется ссылка вида:\n"
                "  `https://t.me/{bot_username}?start=contest_ID`\n"
                "• Пользователь переходит по ссылке и автоматически участвует\n\n"
                "📌 *Администраторам:*\n"
                "• Рассылки НЕ приходят администраторам\n"
                "• Управление конкурсами - в разделе '🎁 Управление конкурсами'\n\n"
                "Чтобы настроить группу для отзывов:\n"
                "1. Добавьте бота в группу как администратора\n"
                "2. Отправьте в группе команду /setreviewsgroup",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send startup message to admin {admin_id}: {e}")

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
