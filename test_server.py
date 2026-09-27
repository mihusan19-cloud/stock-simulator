import concurrent.futures
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

class PlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.db=str(Path(cls.temp.name)/'test.db')
        with socket.socket() as s:
            s.bind(('127.0.0.1',0))
            cls.port=s.getsockname()[1]
        cls.base=f'http://127.0.0.1:{cls.port}'
        cls.process=subprocess.Popen([sys.executable,'server.py'],cwd=Path(__file__).parent,env={**os.environ,'PORT':str(cls.port),'STOCK_DB':cls.db,'HOST':'127.0.0.1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base+'/api/state',timeout=1).close()
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Server failed to start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.temp.cleanup()

    def client(self,name):
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertEqual(self.post(client,'/api/register',{'name':name,'password':'test-password-123'})[0],200)
        return client

    def post(self,client,path,data):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        try:
            with client.open(req,timeout=20) as r:
                return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            return e.code,json.load(e)

    def state(self,client):
        with client.open(self.base+'/api/state') as r:
            return json.load(r)

    def order(self,client,**overrides):
        return self.post(client,'/api/order',{'symbol':'NOVA','side':'buy','kind':'market','quantity':10,**overrides})

    def test_authentication_and_isolation(self):
        alice=self.client('alice')
        bob=self.client('bob')
        self.assertEqual(self.order(alice)[0],200)
        self.assertEqual(self.state(alice)['positions'][0]['quantity'],10)
        self.assertEqual(self.state(bob)['positions'],[])
        self.assertEqual(self.state(bob)['user']['cash'],100000000)
        self.assertEqual(self.post(bob,'/api/login',{'name':'alice','password':'wrong-password'})[0],401)
        self.assertEqual(self.post(alice,'/api/logout',{})[0],200)
        self.assertEqual(self.order(alice)[0],401)
        self.assertEqual(self.post(alice,'/api/login',{'name':'alice','password':'test-password-123'})[0],200)
        self.assertEqual(self.state(alice)['positions'][0]['quantity'],10)

    def test_order_reservations_cancel_and_ownership(self):
        a=self.client('limit-user');b=self.client('other-user')
        self.assertEqual(self.order(a,kind='limit',limit_price=100,quantity=1000000)[0],200)
        order=self.state(a)['orders'][0]
        self.assertEqual(order['status'],'open')
        self.assertEqual(self.order(a)[0],400)
        self.assertEqual(self.post(b,'/api/cancel',{'id':order['id']})[0],400)
        self.assertEqual(self.post(a,'/api/cancel',{'id':order['id']})[0],200)
        self.assertEqual(self.order(a)[0],200)
        self.assertEqual(self.order(a,side='sell',kind='limit',limit_price=100000000,quantity=10)[0],200)
        self.assertEqual(self.order(a,side='sell',quantity=1)[0],400)

    def test_concurrent_spending_is_atomic(self):
        a=self.client('concurrent-user')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results=list(executor.map(lambda _:self.order(a,quantity=5000)[0],range(8)))
        self.assertEqual(results.count(200),1)
        state=self.state(a)
        self.assertGreaterEqual(state['user']['cash'],0)
        self.assertEqual(state['positions'][0]['quantity'],5000)

    def test_validation_and_round_trip(self):
        a=self.client('validation-user')
        for qty in [-1,0,True,1.5,1000001]:
            self.assertEqual(self.order(a,quantity=qty)[0],400)
        self.assertEqual(self.order(a,symbol='MISSING')[0],400)
        self.assertEqual(self.order(a,side='sell')[0],400)
        self.assertEqual(self.order(a)[0],200)
        self.assertEqual(self.order(a,side='sell')[0],200)
        self.assertEqual(self.state(a)['positions'],[])
        self.assertEqual(self.post(a,'/api/watch',{'symbol':'NOVA'})[0],200)
        self.assertIn('NOVA',self.state(a)['watchlist'])

    def test_limit_execution(self):
        a=self.client('execution-user')
        self.assertEqual(self.order(a,kind='limit',limit_price=100,quantity=2)[0],200)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE stocks SET price=100 WHERE symbol='NOVA'")
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            if self.state(a)['orders'][0]['status']=='filled':
                break
            time.sleep(.2)
        self.assertEqual(self.state(a)['orders'][0]['status'],'filled')
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE stocks SET price=12800 WHERE symbol='NOVA'")

    def test_private_files_not_served(self):
        for path in ['/server.py','/data/market.db','/../server.py']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base+path)
            self.assertEqual(error.exception.code,404)

if __name__=='__main__':
    unittest.main(verbosity=2)
