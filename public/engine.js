/* Integer cents, pure game logic. No network or server required. */
(function(root){
 'use strict';
 const companies=[['NOVA','星河科技','科技',12800],['AERO','蒼穹航空','運輸',7650],['LUME','流明能源','能源',9420],['MINT','薄荷生活','消費',4860],['ORBI','環宇通訊','通訊',21500],['TIDE','潮汐金融','金融',6340]];
 const int=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
 function fresh(now=Date.now()){
   const minute=Math.floor(now/60000)*60;
   return {version:1,lastTick:now,nextId:1,user:{name:'單機交易者',cash:100000000},positions:[],orders:[],watchlist:[],stocks:companies.map(([symbol,name,sector,price])=>{
     let previous=price;
     const candles=Array.from({length:90},(_,i)=>{const close=i===89?price:Math.round(price*(1+.015*Math.sin(i/9)+.008*Math.sin(i/3)));const c={ts:minute-(89-i)*60,open:previous,high:Math.max(previous,close)+30,low:Math.min(previous,close)-30,close};previous=close;return c;});
     return {symbol,name,sector,price,opening:price,volume:0,candles};
   })};
 }
 function reserved(s,side,symbol){return s.orders.filter(o=>o.status==='open'&&o.side===side&&(!symbol||o.symbol===symbol)).reduce((a,o)=>a+(side==='buy'?o.quantity*o.limit_price:o.quantity),0);}
 function fill(s,o,price,now){
   let p=s.positions.find(p=>p.symbol===o.symbol);const total=price*o.quantity;
   if(!Number.isSafeInteger(total))throw Error('交易金額超過上限');
   if(o.side==='buy'){
     if(s.user.cash<total)throw Error('可用資金不足');
     if(!p){p={symbol:o.symbol,quantity:0,cost:0};s.positions.push(p);}
     if(!Number.isSafeInteger(p.cost+total)||!Number.isSafeInteger(p.quantity+o.quantity))throw Error('持倉超過上限');
     s.user.cash-=total;p.quantity+=o.quantity;p.cost+=total;
   }else{
     if(!p||p.quantity<o.quantity)throw Error('可賣股數不足');
     if(!Number.isSafeInteger(s.user.cash+total))throw Error('資產超過上限');
     const remain=p.quantity-o.quantity;p.cost=Math.round(p.cost*remain/p.quantity);p.quantity=remain;s.user.cash+=total;
     s.positions=s.positions.filter(p=>p.quantity>0);
   }
   o.status='filled';o.fill_price=price;o.filled=Math.floor(now/1000);
   s.stocks.find(t=>t.symbol===o.symbol).volume+=o.quantity;
 }
 function prune(s){const closed=s.orders.filter(o=>o.status!=='open').slice(-200);s.orders=s.orders.filter(o=>o.status==='open'||closed.includes(o));}
 function tick(s,now=Date.now(),random=Math.random){
   if(now-s.lastTick<3000)return;
   s.lastTick=now;
   for(const stock of s.stocks){
     const before=stock.price;stock.price=Math.max(100,Math.min(100000000,Math.round(before*(1+(Math.floor(random()*101)-50)/20000))));
     const ts=Math.floor(now/60000)*60;let c=stock.candles.at(-1);
     if(c.ts!==ts){c={ts,open:before,high:Math.max(before,stock.price),low:Math.min(before,stock.price),close:stock.price};stock.candles.push(c);stock.candles=stock.candles.slice(-90);}
     else{c.high=Math.max(c.high,stock.price);c.low=Math.min(c.low,stock.price);c.close=stock.price;}
     for(const o of s.orders.filter(o=>o.status==='open'&&o.symbol===stock.symbol)){
       if((o.side==='buy'&&stock.price<=o.limit_price)||(o.side==='sell'&&stock.price>=o.limit_price))fill(s,o,stock.price,now);
     }
   }prune(s);
 }
 function order(s,d,now=Date.now()){
   const stock=s.stocks.find(t=>t.symbol===d.symbol);
   if(!stock||!['buy','sell'].includes(d.side)||!['market','limit'].includes(d.kind)||!int(d.quantity,1,1000000))throw Error('請輸入有效的股票、委託方式與整數股數');
   const price=d.kind==='market'?stock.price:d.limit_price;
   if(!int(price,100,100000000))throw Error('價格需介於 1 與 1,000,000 之間');
   if(s.orders.filter(o=>o.status==='open').length>=100)throw Error('最多保留 100 筆掛單');
   if(d.side==='buy'&&s.user.cash-reserved(s,'buy')<price*d.quantity)throw Error('可用資金不足（含掛單保留金額）');
   const pos=s.positions.find(p=>p.symbol===d.symbol);
   if(d.side==='sell'&&(pos?.quantity||0)-reserved(s,'sell',d.symbol)<d.quantity)throw Error('可賣股數不足（含掛單保留股數）');
   const o={id:s.nextId++,symbol:d.symbol,side:d.side,kind:d.kind,quantity:d.quantity,limit_price:d.kind==='limit'?price:null,status:'open',fill_price:null,created:Math.floor(now/1000),filled:null};
   s.orders.push(o);
   if(d.kind==='market'||(d.side==='buy'&&stock.price<=price)||(d.side==='sell'&&stock.price>=price))fill(s,o,stock.price,now);
   prune(s);
 }
 function act(s,path,d,now=Date.now()){
   if(path==='/api/order')order(s,d,now);
   else if(path==='/api/cancel'){const o=s.orders.find(o=>o.id===d.id&&o.status==='open');if(!o)throw Error('委託已成交或已取消');o.status='cancelled';prune(s);}
   else if(path==='/api/watch'){if(!companies.some(c=>c[0]===d.symbol))throw Error('股票不存在');s.watchlist=s.watchlist.includes(d.symbol)?s.watchlist.filter(x=>x!==d.symbol):[...s.watchlist,d.symbol];}
   else if(path==='rename'){const name=String(d.name||'').trim();if(name.length<1||name.length>24)throw Error('暱稱需 1–24 字');s.user.name=name;}
   else throw Error('操作不存在');
 }
 function snapshot(s){const r=structuredClone(s);r.serverTime=Math.floor(Date.now()/1000);r.orders.reverse();r.leaders=[{name:r.user.name,equity:r.user.cash+r.positions.reduce((a,p)=>a+p.quantity*r.stocks.find(t=>t.symbol===p.symbol).price,0)}];return r;}
 function validate(s){
   const fail=()=>{throw Error('存檔格式不正確或版本不支援，原本進度未變更');};
   if(!s||s.version!==1||!int(s.lastTick)||!int(s.nextId,1)||!s.user||typeof s.user.name!=='string'||s.user.name.length<1||s.user.name.length>24||!int(s.user.cash)||!Array.isArray(s.stocks)||s.stocks.length!==6||!Array.isArray(s.positions)||s.positions.length>6||!Array.isArray(s.orders)||s.orders.length>300||!Array.isArray(s.watchlist)||s.watchlist.length>6)fail();
   const symbols=companies.map(c=>c[0]),unique=a=>new Set(a).size===a.length;
   if(!unique(s.stocks.map(x=>x.symbol))||!unique(s.positions.map(x=>x.symbol))||!unique(s.watchlist)||!unique(s.orders.map(x=>x.id)))fail();
   for(const t of s.stocks){if(!symbols.includes(t.symbol)||!int(t.price,100,100000000)||!int(t.opening,100,100000000)||!int(t.volume)||!Array.isArray(t.candles)||t.candles.length<1||t.candles.length>90)fail();let prev=-1;for(const c of t.candles){if(!int(c.ts)||c.ts<=prev||!['open','high','low','close'].every(k=>int(c[k],1,100000100))||c.high<Math.max(c.open,c.close)||c.low>Math.min(c.open,c.close)||c.low>c.high)fail();prev=c.ts;}}
   for(const p of s.positions)if(!symbols.includes(p.symbol)||!int(p.quantity,1)||!int(p.cost)||!Number.isSafeInteger(p.quantity*100000000))fail();
   for(const o of s.orders)if(!int(o.id,1)||o.id>=s.nextId||!symbols.includes(o.symbol)||!['buy','sell'].includes(o.side)||!['market','limit'].includes(o.kind)||!int(o.quantity,1,1000000)||!['open','filled','cancelled'].includes(o.status)||!int(o.created)||!(o.kind==='limit'?int(o.limit_price,100,100000000):o.limit_price===null)||(o.status==='open'&&o.kind!=='limit')||(o.status==='filled'&&(!int(o.fill_price,100,100000000)||!int(o.filled))))fail();
   if(s.orders.filter(o=>o.status==='open').length>100||s.watchlist.some(x=>!symbols.includes(x))||reserved(s,'buy')>s.user.cash||symbols.some(x=>reserved(s,'sell',x)>(s.positions.find(p=>p.symbol===x)?.quantity||0)))fail();
   const out=structuredClone(s);for(const t of out.stocks){const c=companies.find(c=>c[0]===t.symbol);t.name=c[1];t.sector=c[2];}return out;
 }
 const api={fresh,tick,act,snapshot,validate,reserved};root.OrbitEngine=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
