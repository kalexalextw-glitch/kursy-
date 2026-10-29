import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime

TOKEN = os.environ.get('BOT_TOKEN', '')
ADMINS = {int(x.strip()) for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip()}
DB = os.environ.get('BOT_DB', 'bot.sqlite3')
API = f'https://api.telegram.org/bot{TOKEN}/'
MENU = [['📝 Новый заказ'], ['📋 Мои заказы', '🆘 Поддержка']]
TYPES = [['Научная статья', 'Реферат'], ['Курсовая работа', 'Научная работа'], ['Другое'], ['❌ Отменить']]
STATES = {}


def api(method, **data):
    request = urllib.request.Request(API + method, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=40) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError(result)
    return result['result']


def send(chat, text, keyboard=None):
    data = {'chat_id': chat, 'text': text}
    if keyboard:
        data['reply_markup'] = {'keyboard': keyboard, 'resize_keyboard': True}
    api('sendMessage', **data)


def setup():
    with sqlite3.connect(DB) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, kind TEXT NOT NULL, topic TEXT NOT NULL, deadline TEXT NOT NULL, requirements TEXT, file_id TEXT, status TEXT NOT NULL DEFAULT 'Новая', price INTEGER, created_at TEXT NOT NULL)''')


def describe(order):
    return (f"Заказ №{order['id']}\nВид: {order['kind']}\nТема: {order['topic']}\n"
            f"Срок: {order['deadline']}\nТребования: {order['requirements'] or 'не указаны'}\n"
            f"Методичка: {'прикреплена' if order['file_id'] else 'не приложена'}\n"
            f"Статус: {order['status']}" + (f"\nЦена: {order['price']} ₽" if order['price'] is not None else ''))


def get_order(order_id):
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        return db.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()


def handle(message):
    chat = message['chat']['id']
    if message['chat']['type'] != 'private':
        return
    user = message['from']
    uid = user['id']
    text = message.get('text', '').strip()
    state = STATES.get(uid)
    if text.startswith('/start'):
        STATES.pop(uid, None)
        send(chat, 'Привет! Здесь можно оставить заявку на работу. Выбери «Новый заказ».', MENU)
        return
    if text == '❌ Отменить' or text == '/cancel':
        STATES.pop(uid, None)
        send(chat, 'Оформление отменено.', MENU)
        return
    if uid in ADMINS and text.startswith('/price'):
        parts = text.split(maxsplit=2)
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit() or int(parts[2]) <= 0:
            send(chat, 'Формат: /price НОМЕР СУММА (в целых рублях)')
            return
        order = get_order(int(parts[1]))
        if not order:
            send(chat, 'Заказ не найден.')
            return
        with sqlite3.connect(DB) as db:
            db.execute("UPDATE orders SET price = ?, status = 'Оценён' WHERE id = ?", (int(parts[2]), order['id']))
        try:
            send(order['user_id'], f"Стоимость заказа №{order['id']}: {parts[2]} ₽. По вопросам оплаты свяжись с поддержкой.", MENU)
            send(chat, 'Цена сохранена, клиенту отправлено уведомление.')
        except Exception:
            send(chat, 'Цена сохранена, но уведомить клиента не удалось.')
        return
    if uid in ADMINS and text == '/orders':
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            orders = db.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 20').fetchall()
        send(chat, '\n\n'.join(describe(o) for o in orders) if orders else 'Заказов пока нет.')
        return
    if text == '📝 Новый заказ':
        STATES[uid] = {'step': 'kind'}
        send(chat, 'Шаг 1/4. Выбери вид работы:', TYPES)
        return
    if text == '📋 Мои заказы':
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            orders = db.execute('SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 10', (uid,)).fetchall()
        send(chat, '\n\n'.join(describe(o) for o in orders) if orders else 'У тебя пока нет заказов.', MENU)
        return
    if text == '🆘 Поддержка':
        send(chat, os.environ.get('SUPPORT_TEXT', 'Контакт поддержки пока не настроен.'), MENU)
        return
    if not state:
        send(chat, 'Выбери действие в меню.', MENU)
        return
    step = state['step']
    if step == 'kind':
        if text == 'Другое':
            state['step'] = 'other_kind'
            send(chat, 'Напиши вид работы:', [['❌ Отменить']])
        elif text in ('Научная статья', 'Реферат', 'Курсовая работа', 'Научная работа'):
            state.update(kind=text, step='topic')
            send(chat, 'Шаг 2/4. Напиши тему работы:', [['❌ Отменить']])
        else:
            send(chat, 'Выбери вид работы кнопкой.', TYPES)
    elif step == 'other_kind' and text:
        state.update(kind=text, step='topic')
        send(chat, 'Шаг 2/4. Напиши тему работы:', [['❌ Отменить']])
    elif step == 'topic' and text:
        state.update(topic=text, step='deadline')
        send(chat, 'Шаг 3/4. Укажи срок выполнения, например 25.12.2026:', [['❌ Отменить']])
    elif step == 'deadline' and text:
        try:
            deadline = datetime.strptime(text, '%d.%m.%Y').date()
            if deadline < datetime.now().date():
                raise ValueError()
        except ValueError:
            send(chat, 'Укажи будущую дату в формате ДД.ММ.ГГГГ, например 25.12.2026.')
            return
        state.update(deadline=text, step='requirements')
        send(chat, 'Шаг 4/4. Напиши требования или отправь методичку файлом. Если их нет, нажми «Пропустить».', [['Пропустить'], ['❌ Отменить']])
    elif step == 'requirements':
        document = message.get('document')
        if not text and not document:
            send(chat, 'Отправь текст, файл или нажми «Пропустить».')
            return
        state['requirements'] = None if text == 'Пропустить' else (text or message.get('caption'))
        state['file_id'] = document['file_id'] if document else None
        state['step'] = 'confirm'
        preview = f"Проверь заявку:\nВид: {state['kind']}\nТема: {state['topic']}\nСрок: {state['deadline']}\nТребования: {state['requirements'] or 'не указаны'}\nМетодичка: {'прикреплена' if state['file_id'] else 'не приложена'}"
        send(chat, preview, [['✅ Отправить заявку'], ['❌ Отменить']])
    elif step == 'confirm' and text == '✅ Отправить заявку':
        with sqlite3.connect(DB) as db:
            cursor = db.execute('INSERT INTO orders (user_id, username, kind, topic, deadline, requirements, file_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (uid, user.get('username'), state['kind'], state['topic'], state['deadline'], state.get('requirements'), state.get('file_id'), datetime.now().isoformat()))
            order_id = cursor.lastrowid
        STATES.pop(uid, None)
        send(chat, f'Заявка №{order_id} отправлена! Администратор оценит её и сообщит стоимость.', MENU)
        order = get_order(order_id)
        for admin in ADMINS:
            try:
                send(admin, f"Новая заявка от @{user.get('username', 'без_username')} (ID {uid})\n\n{describe(order)}\n\nНазначить цену: /price {order_id} СУММА")
                if order['file_id']:
                    api('sendDocument', chat_id=admin, document=order['file_id'], caption=f'Методичка к заказу №{order_id}')
            except Exception as exc:
                print('Admin notification failed:', exc, flush=True)
    else:
        send(chat, 'Пожалуйста, следуй подсказке или отмени оформление.')


def main():
    if not TOKEN:
        raise SystemExit('Укажи BOT_TOKEN в переменных окружения.')
    setup()
    offset = 0
    print('Бот запущен.', flush=True)
    while True:
        try:
            for update in api('getUpdates', offset=offset, timeout=25, allowed_updates=['message']):
                offset = update['update_id'] + 1
                if 'message' in update:
                    try:
                        handle(update['message'])
                    except Exception as exc:
                        print('Message error:', exc, flush=True)
        except Exception as exc:
            print('Polling error:', exc, flush=True)
            time.sleep(3)


if __name__ == '__main__':
    main()
