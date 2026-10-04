import hashlib
import hmac
import json
import math
import mimetypes
import os
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('STOCK_DB', str(ROOT / 'data' / 'market.db')))
DB.parent.mkdir(parents=True, exist_ok=True)
INITIAL_CASH = 100000000  # cents
COMPANIES = [('NOVA', '星河科技', '科技', 12800), ('AERO', '蒼穹航空', '運輸', 7650), ('LUME', '流明能源', '能源', 9420), ('MINT', '薄荷生活', '消費', 4860), ('ORBI', '環宇通訊', '通訊', 21500), ('TIDE', '潮汐金融', '金融', 6340)]

def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def initialize():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL, cash INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS stocks(symbol TEXT PRIMARY KEY, name TEXT, sector TEXT, price INTEGER, opening INTEGER, volume INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS candles(symbol TEXT, ts INTEGER, open INTEGER, high INTEGER, low INTEGER, close INTEGER, volume INTEGER, PRIMARY KEY(symbol,ts));
        CREATE TABLE IF NOT EXISTS positions(user_id INTEGER REFERENCES users(id), symbol TEXT REFERENCES stocks(symbol), quantity INTEGER, cost INTEGER, PRIMARY KEY(user_id,symbol));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), symbol TEXT REFERENCES stocks(symbol), side TEXT, kind TEXT, quantity INTEGER, limit_price INTEGER, status TEXT, fill_price INTEGER, created INTEGER, filled INTEGER);
        CREATE TABLE IF NOT EXISTS watchlist(user_id INTEGER REFERENCES users(id), symbol TEXT REFERENCES stocks(symbol), PRIMARY KEY(user_id,symbol));
        ''')
        for symbol, name, sector, price in COMPANIES:
            c.execute('INSERT OR IGNORE INTO stocks(symbol,name,sector,price,opening) VALUES(?,?,?,?,?)', (symbol,name,sector,price,price))
            # Historical demo candles are seeded once, shared by every player.
            if not c.execute('SELECT 1 FROM candles WHERE symbol=? LIMIT 1',(symbol,)).fetchone():
                now = int(time.time()) // 60 * 60
                previous = price
                for i in range(90):
                    close = max(100, round(price * (1 + .015 * math.sin(i / 9) + .008 * math.sin(i / 3)))) if i < 89 else price
                    c.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?)',(symbol,now-(89-i)*60,previous,max(previous,close)+30,min(previous,close)-30,close,100+i*7))
                    previous = close

def fill(c, order, price):
    uid, symbol, qty = order['user_id'], order['symbol'], order['quantity']
    user = c.execute('SELECT cash FROM users WHERE id=?',(uid,)).fetchone()
    pos = c.execute('SELECT * FROM positions WHERE user_id=? AND symbol=?',(uid,symbol)).fetchone()
    total = qty * price
    if order['side'] == 'buy':
        if user['cash'] < total:
            return False
        c.execute('UPDATE users SET cash=cash-? WHERE id=?',(total,uid))
        c.execute('INSERT INTO positions VALUES(?,?,?,?) ON CONFLICT(user_id,symbol) DO UPDATE SET quantity=quantity+excluded.quantity,cost=cost+excluded.cost',(uid,symbol,qty,total))
    else:
        if not pos or pos['quantity'] < qty:
            return False
        remaining = pos['quantity'] - qty
        cost = round(pos['cost'] * remaining / pos['quantity'])
        c.execute('UPDATE users SET cash=cash+? WHERE id=?',(total,uid))
        c.execute('UPDATE positions SET quantity=?,cost=? WHERE user_id=? AND symbol=?',(remaining,cost,uid,symbol))
    c.execute("UPDATE orders SET status='filled',fill_price=?,filled=? WHERE id=?",(price,int(time.time()),order['id']))
    c.execute('UPDATE stocks SET volume=volume+? WHERE symbol=?',(qty,symbol))
    return True

def tick():
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        now = int(time.time()) // 60 * 60
        for stock in c.execute('SELECT * FROM stocks').fetchall():
            p = max(100, round(stock['price'] * (1 + (secrets.randbelow(101)-50)/20000)))
            symbol = stock['symbol']
            c.execute('UPDATE stocks SET price=? WHERE symbol=?',(p,symbol))
            c.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,0) ON CONFLICT(symbol,ts) DO UPDATE SET high=MAX(high,excluded.high),low=MIN(low,excluded.low),close=excluded.close',(symbol,now,stock['price'],max(p,stock['price']),min(p,stock['price']),p))
            pending = c.execute("SELECT * FROM orders WHERE symbol=? AND status='open' ORDER BY id",(symbol,)).fetchall()
            for order in pending:
                if (order['side']=='buy' and p<=order['limit_price']) or (order['side']=='sell' and p>=order['limit_price']):
                    if not fill(c,order,p):
                        c.execute("UPDATE orders SET status='rejected' WHERE id=?",(order['id'],))
        c.execute('DELETE FROM candles WHERE ts<?',(now-86400*7,))
        c.execute('DELETE FROM sessions WHERE expires<?',(int(time.time()),))

def market_loop():
    while True:
        time.sleep(3)
        try:
            tick()
        except Exception as exc:
            print('Market tick failed:', exc, flush=True)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args)>1 else '') not in ('200','304'):
            super().log_message(fmt,*args)

    def respond(self, status, data, cookie=None):
        payload = json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if cookie:
            self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def user(self,c):
        cookie=SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie',''))
            token=cookie['session'].value if 'session' in cookie else ''
        except Exception:
            return None
        return c.execute('SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?',(token,int(time.time()))).fetchone()

    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/state':
            with connect() as c:
                c.execute('BEGIN')
                stocks=[dict(r) for r in c.execute('SELECT * FROM stocks')]
                for stock in stocks:
                    stock['candles']=[dict(r) for r in c.execute('SELECT * FROM (SELECT * FROM candles WHERE symbol=? ORDER BY ts DESC LIMIT 90) ORDER BY ts',(stock['symbol'],))]
                user=self.user(c)
                data={'stocks':stocks,'serverTime':int(time.time()),'user':None}
                if user:
                    uid=user['id']
                    data.update(user={'name':user['name'],'cash':user['cash']},positions=[dict(r) for r in c.execute('SELECT * FROM positions WHERE user_id=? AND quantity>0',(uid,))],orders=[dict(r) for r in c.execute("SELECT * FROM orders WHERE user_id=? AND (status='open' OR id IN (SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 200)) ORDER BY id DESC",(uid,uid))],watchlist=[r['symbol'] for r in c.execute('SELECT symbol FROM watchlist WHERE user_id=?',(uid,))])
                data['leaders']=[dict(r) for r in c.execute('SELECT u.name,u.cash+COALESCE(SUM(p.quantity*s.price),0) AS equity FROM users u LEFT JOIN positions p ON p.user_id=u.id LEFT JOIN stocks s ON s.symbol=p.symbol GROUP BY u.id ORDER BY equity DESC LIMIT 10')]
            return self.respond(200,data)
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/pastel.css':'pastel.css'}
        if path not in files:
            return self.respond(404,{'error':'找不到頁面'})
        target=ROOT/'public'/files[path]
        body=target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',(mimetypes.guess_type(target)[0] or 'text/plain')+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self.respond(403,{'error':'不允許跨站請求'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.respond(415,{'error':'請使用 JSON'})
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>8192:
                return self.respond(400,{'error':'請求大小不正確'})
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):
                raise ValueError('請求格式不正確')
            path=urlparse(self.path).path
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if path in ('/api/register','/api/login'):
                    name=data.get('name','')
                    password=data.get('password','')
                    if not isinstance(name,str) or not isinstance(password,str):
                        raise ValueError('帳號或密碼格式不正確')
                    name=name.strip()
                    if not 2<=len(name)<=24 or not 8<=len(password)<=128:
                        raise ValueError('帳號需 2–24 字，密碼需 8–128 字')
                    if path=='/api/register':
                        salt=secrets.token_hex(16)
                        digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
                        try:
                            uid=c.execute('INSERT INTO users(name,salt,password,cash) VALUES(?,?,?,?)',(name,salt,digest,INITIAL_CASH)).lastrowid
                        except sqlite3.IntegrityError:
                            raise ValueError('這個帳號名稱已被使用')
                    else:
                        user=c.execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
                        salt=user['salt'] if user else '00'*16
                        digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
                        if not user or not hmac.compare_digest(digest,user['password']):
                            return self.respond(401,{'error':'帳號或密碼不正確'})
                        uid=user['id']
                    token=secrets.token_urlsafe(32)
                    c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,int(time.time())+604800))
                    c.commit()
                    secure='; Secure' if os.environ.get('COOKIE_SECURE')=='1' else ''
                    return self.respond(200,{'ok':True},f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800{secure}')
                user=self.user(c)
                if not user:
                    return self.respond(401,{'error':'請先登入'})
                uid=user['id']
                if path=='/api/logout':
                    cookie=SimpleCookie(self.headers.get('Cookie',''))
                    c.execute('DELETE FROM sessions WHERE token=?',(cookie['session'].value,))
                    c.commit()
                    return self.respond(200,{'ok':True},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if path=='/api/watch':
                    symbol=data.get('symbol')
                    if not c.execute('SELECT 1 FROM stocks WHERE symbol=?',(symbol,)).fetchone():
                        raise ValueError('股票不存在')
                    if c.execute('SELECT 1 FROM watchlist WHERE user_id=? AND symbol=?',(uid,symbol)).fetchone():
                        c.execute('DELETE FROM watchlist WHERE user_id=? AND symbol=?',(uid,symbol))
                    else:
                        c.execute('INSERT INTO watchlist VALUES(?,?)',(uid,symbol))
                elif path=='/api/cancel':
                    result=c.execute("UPDATE orders SET status='cancelled' WHERE id=? AND user_id=? AND status='open'",(data.get('id'),uid))
                    if result.rowcount!=1:
                        raise ValueError('委託已成交、已取消或不存在')
                elif path=='/api/order':
                    symbol,side,kind,qty=data.get('symbol'),data.get('side'),data.get('kind'),data.get('quantity')
                    if side not in ('buy','sell') or kind not in ('market','limit') or type(qty)!=int or not 1<=qty<=1000000:
                        raise ValueError('請輸入有效的交易類型與整數股數（1–1,000,000）')
                    stock=c.execute('SELECT * FROM stocks WHERE symbol=?',(symbol,)).fetchone()
                    if not stock:
                        raise ValueError('股票不存在')
                    price=data.get('limit_price') if kind=='limit' else stock['price']
                    if type(price)!=int or not 100<=price<=100000000:
                        raise ValueError('價格需介於 1 與 1,000,000 之間')
                    if c.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='open'",(uid,)).fetchone()[0]>=100:
                        raise ValueError('最多同時保留 100 筆掛單')
                    if side=='buy':
                        reserved=c.execute("SELECT COALESCE(SUM(quantity*limit_price),0) FROM orders WHERE user_id=? AND side='buy' AND status='open'",(uid,)).fetchone()[0]
                        if user['cash']-reserved<qty*price:
                            raise ValueError('可用資金不足（含掛單保留金額）')
                    else:
                        pos=c.execute('SELECT quantity FROM positions WHERE user_id=? AND symbol=?',(uid,symbol)).fetchone()
                        reserved=c.execute("SELECT COALESCE(SUM(quantity),0) FROM orders WHERE user_id=? AND symbol=? AND side='sell' AND status='open'",(uid,symbol)).fetchone()[0]
                        if (pos['quantity'] if pos else 0)-reserved<qty:
                            raise ValueError('可賣股數不足（含掛單保留股數）')
                    oid=c.execute("INSERT INTO orders(user_id,symbol,side,kind,quantity,limit_price,status,created) VALUES(?,?,?,?,?,?,'open',?)",(uid,symbol,side,kind,qty,price if kind=='limit' else None,int(time.time()))).lastrowid
                    order=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                    if kind=='market' or (side=='buy' and stock['price']<=price) or (side=='sell' and stock['price']>=price):
                        if not fill(c,order,stock['price']):
                            raise ValueError('資金或持股不足')
                else:
                    return self.respond(404,{'error':'找不到操作'})
                c.commit()
                return self.respond(200,{'ok':True})
        except (ValueError,TypeError,OverflowError) as exc:
            return self.respond(400,{'error':str(exc) or '輸入不正確'})
        except Exception as exc:
            print('Request failed:',repr(exc),flush=True)
            return self.respond(500,{'error':'伺服器暫時無法完成操作'})

if __name__=='__main__':
    initialize()
    threading.Thread(target=market_loop,daemon=True).start()
    port=int(os.environ.get('PORT','8088'))
    print(f'Orbit Exchange running at http://localhost:{port}',flush=True)
    ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),port),Handler).serve_forever()
