import requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from config import BINANCE_BASE_URL, OI_WORKERS
from database import get_connection, init_db

HOURS=97

def symbols():
    r=requests.get(f'{BINANCE_BASE_URL}/fapi/v1/exchangeInfo',timeout=30); r.raise_for_status()
    return [x['symbol'] for x in r.json()['symbols'] if x['status']=='TRADING' and x['contractType']=='PERPETUAL' and x['quoteAsset']=='USDT']

def market():
    r=requests.get(f'{BINANCE_BASE_URL}/fapi/v1/premiumIndex',timeout=30); r.raise_for_status(); d=r.json(); d=[d] if isinstance(d,dict) else d
    return {x['symbol']:(float(x['markPrice']),float(x.get('lastFundingRate',0))) for x in d}

def one(symbol, start_ms, end_ms, prices):
    r=requests.get(f'{BINANCE_BASE_URL}/futures/data/openInterestHist',params={'symbol':symbol,'period':'1h','startTime':start_ms,'endTime':end_ms,'limit':HOURS},timeout=30)
    if r.status_code != 200: return symbol, [], f'HTTP {r.status_code}'
    rows=[]
    for x in r.json():
        ts=int(x['timestamp'])//1000
        oi=float(x['sumOpenInterest'])
        usd=float(x.get('sumOpenInterestValue',0))
        if usd<=0: usd=oi*prices.get(symbol,(0,0))[0]
        price,funding=prices.get(symbol,(0,0))
        rows.append((symbol,ts,price,oi,usd,funding))
    return symbol,rows,None

def main():
    init_db(); syms=symbols(); prices=market(); now=int(time.time()*1000); start=now-(HOURS+2)*3600*1000
    all_rows=[]; errors=0; done=0
    with ThreadPoolExecutor(max_workers=min(8,OI_WORKERS)) as ex:
        fs=[ex.submit(one,s,start,now,prices) for s in syms]
        for f in as_completed(fs):
            sym,rows,err=f.result(); done+=1
            if err: errors+=1; print(sym,err)
            all_rows.extend(rows)
            if done%50==0: print(f'进度 {done}/{len(syms)}')
    conn=get_connection(); conn.executemany('''INSERT OR REPLACE INTO snapshots(symbol,timestamp,price,open_interest,open_interest_usd,funding_rate) VALUES (?,?,?,?,?,?)''',all_rows); conn.commit(); conn.close()
    print(f'历史初始化完成：{len(all_rows)} 条，合约 {len(syms)}，失败 {errors}')

if __name__=='__main__': main()
