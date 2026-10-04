/* IndexedDB read/write transactions serialize mutations across tabs. */
globalThis.OrbitStore=(function(){
 let dbPromise;
 function db(){return dbPromise||(dbPromise=new Promise((resolve,reject)=>{
   const req=indexedDB.open('orbit-browser-singleplayer-v1',1);
   req.onupgradeneeded=()=>req.result.createObjectStore('game');
   req.onsuccess=()=>resolve(req.result);
   req.onerror=()=>reject(Error('無法開啟本機存檔，請允許瀏覽器儲存網站資料'));
   req.onblocked=()=>reject(Error('請關閉其他星軌分頁再重試'));
 }));}
 async function transaction(action,replace=false){const connection=await db();return new Promise((resolve,reject)=>{
   const tx=connection.transaction('game','readwrite'),store=tx.objectStore('game');let result,error;
   tx.oncomplete=()=>resolve(result);tx.onabort=()=>reject(error||Error('存檔失敗，可能儲存空間不足；本次操作未儲存'));
   tx.onerror=()=>{};
   const req=store.get('current');req.onsuccess=()=>{try{const state=!replace&&req.result?OrbitEngine.validate(req.result):OrbitEngine.fresh();result=action(state);store.put(result,'current');}catch(e){error=e;tx.abort();}};
 });}
 return {
   read:()=>transaction(s=>s),
   tick:()=>transaction(s=>{OrbitEngine.tick(s);return s;}),
   act:(path,data)=>transaction(s=>{OrbitEngine.act(s,path,data);return s;}),
   import:raw=>{const valid=OrbitEngine.validate(raw);valid.lastTick=Date.now();return transaction(()=>valid,true);},
   reset:()=>transaction(()=>OrbitEngine.fresh(),true)
 };
})();
