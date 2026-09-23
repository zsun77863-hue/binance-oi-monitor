import argparse, json, os, random, threading, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from config import BINANCE_BASE_URL, MIN_OI_USD, APP_TIMEZONE
from database import get_connection, init_db

ROOT=Path('/opt/oi-monitor'); DATA=ROOT/'data'; CACHE=DATA/'symbols.json'; LOCK=ROOT/'collector.lock'
RATE_INTERVAL=0.58
MAX_RETRIES=3
_thread_lock=threading.Lock(); _last_request=0.0; _cooldown_until=0.0
rate_events=0
session=requests.Session(); session.headers.update({'User-Agent':'oi-monitor/2.0'})

def log(msg): print(datetime.now().isoformat(timespec='seconds'), msg, flush=True)

def request_json(endpoint, params=None, symbol='-', attempts=4):
    global _last_request,_cooldown_until,rate_events
    backoff=[5,10,20,40]
    for attempt in range(attempts):
        with _thread_lock:
            wait=max(0.0,_cooldown_until-time.time(),_last_request+RATE_INTERVAL-time.time())
            if wait: time.sleep(wait)
            _last_request=time.time()
        started=time.monotonic()
        try:
            r=session.get(f'{BINANCE_BASE_URL}{endpoint}',params=params,timeout=20)
            elapsed=time.monotonic()-started; retry=r.headers.get('Retry-After'); w1=r.headers.get('X-MBX-USED-WEIGHT-1M','-'); w1s=r.headers.get('X-MBX-USED-WEIGHT-1S','-')
            if r.status_code==200:
                log(f'BINANCE {endpoint} symbol={symbol} status=200 elapsed={elapsed:.2f}s weight_1m={w1} weight_1s={w1s}')
                return r.json()
            if r.status_code in (429,418):
                rate_events+=1
                if r.status_code==429:
                    wait=float(retry) if retry and retry.replace('.','',1).isdigit() else backoff[min(attempt,3)]
                    log(f'BINANCE RATE LIMIT 429 symbol={symbol} Retry-After={retry or "none"} cooldown={wait}s weight_1m={w1} weight_1s={w1s}')
                else:
                    wait=float(retry) if retry and retry.replace('.','',1).isdigit() else 180
                    log(f'BINANCE IP BAN 418 symbol={symbol} Retry-After={retry or "none"} cooldown={wait}s weight_1m={w1} weight_1s={w1s}')
                _cooldown_until=time.time()+wait+random.uniform(1,4)
                continue
            log(f'BINANCE ERROR {endpoint} symbol={symbol} status={r.status_code} elapsed={elapsed:.2f}s weight_1m={w1} weight_1s={w1s}')
        except Exception as e:
            log(f'BINANCE EXCEPTION {endpoint} symbol={symbol} error={e}')
        time.sleep(backoff[min(attempt,3)]+random.uniform(0,2))
    return None

def fetch_symbols(refresh=False):
    DATA.mkdir(parents=True,exist_ok=True)
    if CACHE.exists() and not refresh:
        try:
            obj=json.loads(CACHE.read_text()); age=time.time()-CACHE.stat().st_mtime
            if age<86400 and obj: return obj
        except Exception: pass
    data=request_json('/fapi/v1/exchangeInfo',symbol='exchangeInfo')
    if data:
        obj=[{'symbol':x['symbol'],'baseAsset':x['baseAsset'],'quoteAsset':x['quoteAsset'],'contractType':x['contractType'],'status':x['status']} for x in data['symbols'] if x['status']=='TRADING' and x['contractType']=='PERPETUAL' and x['quoteAsset']=='USDT']
        CACHE.write_text(json.dumps(obj,ensure_ascii=False,indent=2)); log(f'symbol cache refreshed: {len(obj)}')
        return obj
    if CACHE.exists():
        obj=json.loads(CACHE.read_text()); log(f'exchangeInfo unavailable, using cached symbols: {len(obj)}'); return obj
    raise RuntimeError('no symbol cache and exchangeInfo unavailable')

def ensure_schema():
    conn=get_connection(); conn.execute('''CREATE TABLE IF NOT EXISTS collection_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, scheduled_hour INTEGER UNIQUE, started_at TEXT, finished_at TEXT, expected_symbols INTEGER, success_symbols INTEGER, failed_symbols INTEGER, status TEXT, rate_limit_events INTEGER DEFAULT 0, error_message TEXT)'''); conn.commit(); conn.close()

def scheduled_hour():
    now=datetime.now(ZoneInfo(APP_TIMEZONE)); return int(now.replace(minute=0,second=0,microsecond=0).timestamp())

def collect(refresh=False):
    global rate_events
    rate_events=0; init_db(); ensure_schema(); scheduled=scheduled_hour(); started=datetime.now(timezone.utc).isoformat(); symbols=fetch_symbols(refresh); expected=len(symbols)
    conn=get_connection(); conn.execute('INSERT OR REPLACE INTO collection_runs(scheduled_hour,started_at,finished_at,expected_symbols,success_symbols,failed_symbols,status,rate_limit_events,error_message) VALUES(?,?,?,?,?,?,?,?,?)',(scheduled,started,None,expected,0,expected,'RUNNING',0,None)); conn.commit(); conn.close()
    log(f'COLLECTION START scheduled={scheduled} expected={expected} rate_interval={RATE_INTERVAL}s')
    prices=request_json('/fapi/v1/premiumIndex',symbol='premiumIndex') or []
    prices={x['symbol']:{'price':float(x['markPrice']),'funding_rate':float(x.get('lastFundingRate',0))} for x in (prices if isinstance(prices,list) else [prices])}
    pending=[x['symbol'] for x in symbols]; results=[]
    for round_no,delay in enumerate([0,5,15,30],1):
        if round_no>1 and not pending: break
        if round_no>1: log(f'retry round={round_no} pending={len(pending)} sleep={delay}s'); time.sleep(delay)
        next_pending=[]
        for idx,sym in enumerate(pending,1):
            payload=request_json('/fapi/v1/openInterest',{'symbol':sym},sym)
            if payload and sym in prices:
                oi=float(payload['openInterest']); price=prices[sym]['price']; usd=oi*price
                if MIN_OI_USD<=0 or usd>=MIN_OI_USD: results.append((sym,scheduled,price,oi,usd,prices[sym]['funding_rate']))
            else: next_pending.append(sym)
            if idx%50==0: log(f'progress round={round_no} processed={idx}/{len(pending)} success={len(results)} failed={len(next_pending)}')
        pending=next_pending
    conn=get_connection(); conn.executemany('INSERT OR REPLACE INTO snapshots(symbol,timestamp,price,open_interest,open_interest_usd,funding_rate) VALUES(?,?,?,?,?,?)',results)
    status='COMPLETE' if not pending and len(results)==expected else ('PARTIAL' if results else 'FAILED'); finished=datetime.now(timezone.utc).isoformat(); err=','.join(pending[:20]) if pending else None
    conn.execute('INSERT OR REPLACE INTO collection_runs(scheduled_hour,started_at,finished_at,expected_symbols,success_symbols,failed_symbols,status,rate_limit_events,error_message) VALUES(?,?,?,?,?,?,?,?,?)',(scheduled,started,finished,expected,len(results),len(pending),status,rate_events,err)); conn.commit(); conn.close(); log(f'COLLECTION FINISH scheduled={scheduled} expected={expected} success={len(results)} failed={len(pending)} status={status} rate_limit_events={rate_events}')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--refresh-symbols',action='store_true'); args=ap.parse_args()
    import fcntl
    with open(LOCK,'w') as lf:
        try: fcntl.flock(lf,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: log('COLLECTION SKIP another collector is running'); return
        collect(args.refresh_symbols)
if __name__=='__main__': main()
