import requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import BINANCE_BASE_URL, OI_WORKERS
from database import get_connection

def symbols():
 r=requests.get(f'{BINANCE_BASE_URL}/fapi/v1/exchangeInfo',timeout=30); r.raise_for_status(); return [x['symbol'] for x in r.json()['symbols'] if x['status']=='TRADING' and x['contractType']=='PERPETUAL' and x['quoteAsset']=='USDT']
def one(symbol,timestamps):
 start=min(timestamps)*1000; end=(max(timestamps)+3600)*1000
 r=requests.get(f'{BINANCE_BASE_URL}/fapi/v1/markPriceKlines',params={'symbol':symbol,'interval':'1h','startTime':start,'endTime':end,'limit':1500},timeout=30)
 if r.status_code!=200:return symbol,{},f'HTTP {r.status_code}'
 return symbol,{int(k[0])//1000:float(k[4]) for k in r.json()},None
def main():
 conn=get_connection(); ts=[r[0] for r in conn.execute('SELECT DISTINCT timestamp FROM snapshots').fetchall()]; syms=symbols(); updates=[]; errors=0; done=0
 with ThreadPoolExecutor(max_workers=min(8,OI_WORKERS)) as ex:
  fs=[ex.submit(one,s,ts) for s in syms]
  for f in as_completed(fs):
   sym,prices,err=f.result(); done+=1
   if err: errors+=1
   rows=conn.execute('SELECT timestamp,open_interest FROM snapshots WHERE symbol=?',(sym,)).fetchall()
   for r in rows:
    price=prices.get(r['timestamp'])
    if price and price>0: updates.append((price,r['open_interest']*price,sym,r['timestamp']))
   if done%50==0: print(f'进度 {done}/{len(syms)}')
 conn.executemany('UPDATE snapshots SET price=?, open_interest_usd=? WHERE symbol=? AND timestamp=?',updates); conn.commit(); conn.close(); print(f'价格回填完成：{len(updates)} 条，失败 {errors}')
if __name__=='__main__':main()
