"""Small HTTPS-proxy-compatible API for the Kursio Mini App."""
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse
from store import Store

TOKEN = os.environ.get('BOT_TOKEN', '')
ORIGIN = os.environ.get('APP_ORIGIN', 'https://kalexalextw-glitch.github.io')
ADMIN_IDS = {int(x) for x in os.environ.get('ADMIN_IDS', '').split(',') if x.strip()}
store = None


def verify_init_data(raw, token, timestamp=None):
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=50)
    if len(dict(pairs)) != len(pairs):
        raise ValueError('Duplicate fields')
    values = dict(pairs)
    supplied = values.pop('hash', '')
    check = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise ValueError('Invalid signature')
    auth = int(values['auth_date'])
    current = int(time.time() if timestamp is None else timestamp)
    if auth > current + 60 or current - auth > 86400:
        raise ValueError('Expired authentication')
    user = json.loads(values['user'])
    if type(user.get('id')) is not int or user['id'] <= 0:
        raise ValueError('Invalid user')
    return user, values.get('start_param', '')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Vary', 'Origin')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Telegram-Init-Data')
        self.send_header('Vary', 'Origin')
        self.end_headers()

    def user(self):
        user, start = verify_init_data(self.headers.get('X-Telegram-Init-Data', ''), TOKEN)
        name = ' '.join(filter(None, (user.get('first_name'), user.get('last_name')))) or 'Пользователь'
        store.register_user(user['id'], name, start[4:] if start.startswith('ref_') else None)
        return user

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            return self.respond(200, {'ok': True})
        try:
            user = self.user()
            if path == '/api/profile':
                return self.respond(200, {'profile': store.profile(user['id'])})
            if path == '/api/orders':
                return self.respond(200, {'orders': store.list_orders(user['id'])})
            return self.respond(404, {'error': 'Not found'})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self.respond(401, {'error': 'Authentication failed'})

    def do_POST(self):
        try:
            user = self.user()
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self.respond(401, {'error': 'Authentication failed'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 16384:
                raise ValueError('Invalid request')
            body = json.loads(self.rfile.read(length))
            path = urlparse(self.path).path
            if path == '/api/orders':
                order_id = store.create_order(user['id'], body.get('kind'), body.get('topic'), body.get('deadline'), body.get('details', ''))
                return self.respond(201, {'id': order_id})
            if path == '/api/redeem':
                store.redeem(user['id'], body['order_id'], body['amount_kopecks'])
                return self.respond(200, {'ok': True})
            if path.startswith('/api/admin/'):
                if user['id'] not in ADMIN_IDS:
                    return self.respond(403, {'error': 'Forbidden'})
                if path == '/api/admin/quote':
                    store.quote(body['order_id'], body['price_kopecks'])
                elif path == '/api/admin/payment':
                    store.confirm_payment(body['order_id'], body['paid_kopecks'])
                elif path == '/api/admin/status':
                    store.change_status(body['order_id'], body['status'])
                else:
                    return self.respond(404, {'error': 'Not found'})
                return self.respond(200, {'ok': True})
            return self.respond(404, {'error': 'Not found'})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self.respond(400, {'error': 'Invalid request or action'})


def main():
    global store
    if not TOKEN:
        raise SystemExit('BOT_TOKEN is required')
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise SystemExit('DATABASE_URL is required for persistent PostgreSQL storage')
    from postgres_store import PostgresStore
    store = PostgresStore(database_url)
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), Handler).serve_forever()

if __name__ == '__main__':
    main()
