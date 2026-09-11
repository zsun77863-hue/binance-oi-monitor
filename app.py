from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from database import init_db, get_connection
from config import RANKING_LIMIT, APP_TIMEZONE
from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel

app=FastAPI(title='Binance OI Monitor')
PERIODS={'4H':4,'12H':12,'24H':24,'48H':48,'72H':72,'96H':96}

def latest():
    conn=get_connection(); row=conn.execute('SELECT MAX(timestamp) FROM snapshots').fetchone(); conn.close(); return row[0] if row and row[0] is not None else None

def ranking(period, limited=True):
    hours=PERIODS[period]; now=latest()
    if now is None:return {'period':period,'timestamp':None,'data':[],'message':'正在建立历史数据...'}
    old=now-hours*3600; conn=get_connection()
    cur=conn.execute('SELECT symbol,price,open_interest,open_interest_usd,funding_rate FROM snapshots WHERE timestamp=?',(now,)).fetchall(); out=[]
    history=conn.execute('SELECT symbol,price,open_interest,open_interest_usd,timestamp FROM snapshots WHERE timestamp BETWEEN ? AND ?', (old,now)).fetchall()
    grouped={}
    for h in history: grouped.setdefault(h['symbol'],[]).append(h)
    for c in cur:
        p=conn.execute('SELECT price,open_interest,open_interest_usd,timestamp FROM snapshots WHERE symbol=? AND timestamp BETWEEN ? AND ? ORDER BY ABS(timestamp-?) LIMIT 1',(c['symbol'],old-300,old+300,old)).fetchone()
        if not p or p['open_interest']<=0 or p['open_interest_usd']<=0: continue
        rows=grouped.get(c['symbol'],[])
        if not rows: continue
        oi_pct=(c['open_interest']/p['open_interest']-1)*100; oi_amount=c['open_interest']-p['open_interest']
        usd_pct=(c['open_interest_usd']/p['open_interest_usd']-1)*100; usd_amount=c['open_interest_usd']-p['open_interest_usd']
        price_ok=c['price'] is not None and p['price'] is not None and c['price']>0 and p['price']>0
        price_pct=(c['price']/p['price']-1)*100 if price_ok else None
        if usd_pct>=50 and price_pct is not None and price_pct<0: divergence='strong'
        elif usd_pct>=50 and price_pct is not None and 0<=price_pct<=5: divergence='flat'
        elif usd_pct>0 and price_pct is not None and price_pct>5: divergence='sync'
        else: divergence='none'
        min_oi=min(x['open_interest'] for x in rows); max_oi=max(x['open_interest'] for x in rows)
        min_usd=min(x['open_interest_usd'] for x in rows); max_usd=max(x['open_interest_usd'] for x in rows)
        out.append({'symbol':c['symbol'],'period':period,'current_price':c['price'],'historical_price':p['price'],'price_data_available':price_ok,'price_change_percent':price_pct,'current_oi':c['open_interest'],'historical_oi':p['open_interest'],'oi_change_percent':oi_pct,'oi_quantity_change_percent':oi_pct,'oi_quantity_change_amount':oi_amount,'oi_change_amount':oi_amount,'current_oi_usd':c['open_interest_usd'],'historical_oi_usd':p['open_interest_usd'],'oi_change_usd_percent':usd_pct,'oi_change_usd_amount':usd_amount,'oi_value_change_percent':usd_pct,'oi_value_change_amount':usd_amount,'min_oi':min_oi,'max_oi':max_oi,'min_oi_usd':min_usd,'max_oi_usd':max_usd,'divergence':divergence,'funding_rate':c['funding_rate'],'price':c['price'],'open_interest':c['open_interest'],'open_interest_usd':c['open_interest_usd'],'oi_usd_percent':usd_pct,'oi_usd_change':usd_amount,'price_change':price_pct})
    conn.close(); out.sort(key=lambda x:x['oi_quantity_change_percent'],reverse=True)
    return {'period':period,'timestamp':datetime.fromtimestamp(now,ZoneInfo(APP_TIMEZONE)).isoformat(),'data':out[:RANKING_LIMIT] if limited else out}

def gold_dog(hours):
    period=f'{hours}H'; result=ranking(period, limited=False)
    data=[x for x in result.get('data',[]) if x.get('oi_quantity_change_percent',-1)>30 and x.get('price_data_available') and x.get('price_change_percent') is not None and -30<=x['price_change_percent']<=30]
    data.sort(key=lambda x:x['oi_quantity_change_percent'],reverse=True)
    result['period']=f'金狗{hours}H'; result['strategy_hours']=hours; result['strategy']='OI数量涨幅 > 30%，价格变化在 -30% 到 +30%'; result['qualified_count']=len(data); result['data']=data
    return result

class WatchSymbol(BaseModel):
    symbol: str

def ensure_watchlist():
    conn=get_connection(); conn.execute("CREATE TABLE IF NOT EXISTS watchlist (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"); conn.commit(); conn.close()

def iso_ts(ts): return datetime.fromtimestamp(ts,ZoneInfo(APP_TIMEZONE)).isoformat() if ts is not None else None

def watch_history(symbol, hours=72):
    hours=max(1,min(int(hours),72)); conn=get_connection(); latest_ts=latest()
    if latest_ts is None: conn.close(); return {'symbol':symbol,'hours':hours,'data':[],'latest_timestamp':None}
    rows=conn.execute('SELECT timestamp,open_interest,open_interest_usd,price FROM snapshots WHERE symbol=? AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC',(symbol,latest_ts-(hours-1)*3600,latest_ts)).fetchall(); conn.close()
    by_ts={r['timestamp']:r for r in rows}; data=[]
    for i in range(hours):
        ts=latest_ts-i*3600; r=by_ts.get(ts); older=by_ts.get(ts-3600)
        item={'timestamp':iso_ts(ts),'oi_quantity':r['open_interest'] if r else None,'oi_value':r['open_interest_usd'] if r else None,'price':r['price'] if r else None,'missing':r is None,'oi_quantity_change':None,'oi_value_change':None,'price_change_percent':None}
        if r and older:
            item['oi_quantity_change']=r['open_interest']-older['open_interest']; item['oi_value_change']=r['open_interest_usd']-older['open_interest_usd']; item['price_change_percent']=(r['price']/older['price']-1)*100 if r['price'] and older['price'] else None
        data.append(item)
    return {'symbol':symbol,'hours':hours,'latest_timestamp':iso_ts(latest_ts),'data':data}

@app.get('/api/watchlist')
def get_watchlist():
    ensure_watchlist(); conn=get_connection(); ws=conn.execute('SELECT symbol,created_at FROM watchlist ORDER BY id').fetchall(); out=[]
    for w in ws:
        r=conn.execute('SELECT timestamp,price,open_interest,open_interest_usd FROM snapshots WHERE symbol=? ORDER BY timestamp DESC LIMIT 1',(w['symbol'],)).fetchone()
        out.append({'symbol':w['symbol'],'created_at':w['created_at'],'timestamp':iso_ts(r['timestamp']) if r else None,'price':r['price'] if r else None,'oi_quantity':r['open_interest'] if r else None,'oi_value':r['open_interest_usd'] if r else None})
    conn.close(); return {'data':out}

@app.post('/api/watchlist')
def add_watchlist(body: WatchSymbol):
    ensure_watchlist(); symbol=body.symbol.strip().upper(); conn=get_connection(); exists=conn.execute('SELECT 1 FROM snapshots WHERE symbol=? LIMIT 1',(symbol,)).fetchone()
    if not exists: conn.close(); raise HTTPException(404,'symbol 不存在于现有数据库')
    try: conn.execute('INSERT INTO watchlist(symbol,created_at) VALUES(?,?)',(symbol,datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat())); conn.commit()
    except Exception: conn.close(); raise HTTPException(409,f'{symbol} 已经在自选列表')
    conn.close(); return {'ok':True,'symbol':symbol}

@app.delete('/api/watchlist/{symbol}')
def delete_watchlist(symbol:str):
    ensure_watchlist(); conn=get_connection(); conn.execute('DELETE FROM watchlist WHERE symbol=?',(symbol.upper(),)); conn.commit(); conn.close(); return {'ok':True,'symbol':symbol.upper()}

@app.get('/api/watchlist/{symbol}/history')
def watchlist_history(symbol:str,hours:int=72):
    symbol=symbol.upper(); conn=get_connection(); exists=conn.execute('SELECT 1 FROM snapshots WHERE symbol=? LIMIT 1',(symbol,)).fetchone(); conn.close()
    if not exists: raise HTTPException(404,'symbol 不存在于现有数据库')
    return watch_history(symbol,hours)

@app.get('/api/ranking/gold-dog')
def api_gold_dog(hours:int=48): return gold_dog(hours) if hours in (24,48) else {'error':'hours must be 24 or 48'}

@app.get('/api/ranking/{period}')
def api_ranking(period:str): return ranking(period) if period in PERIODS else {'error':'invalid period'}
@app.get('/api/collector/status')
def collector_status():
    conn=get_connection()
    try:
        row=conn.execute('SELECT scheduled_hour,started_at,finished_at,expected_symbols,success_symbols,failed_symbols,status,rate_limit_events,error_message FROM collection_runs ORDER BY scheduled_hour DESC LIMIT 1').fetchone()
    except Exception:
        row=None
    conn.close()
    if not row: return {'status':'UNKNOWN','expected':0,'success':0,'failed':0,'rate_limit_events':0}
    d=dict(row); d['scheduled_hour']=datetime.fromtimestamp(d['scheduled_hour'],ZoneInfo(APP_TIMEZONE)).isoformat() if d.get('scheduled_hour') else None
    d['expected']=d.pop('expected_symbols'); d['success']=d.pop('success_symbols'); d['failed']=d.pop('failed_symbols'); return d

@app.get('/api/status')
def status():
    now=latest(); conn=get_connection(); count=conn.execute('SELECT COUNT(*) FROM snapshots WHERE timestamp=?',(now,)).fetchone()[0] if now else 0; conn.close(); return {'latest_timestamp':now,'symbols':count}
@app.get('/',response_class=HTMLResponse)
def home(): return HTML
if __name__=='__main__':
    init_db(); import uvicorn; uvicorn.run(app,host='127.0.0.1',port=8000)

HTML="""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Binance Futures OI Ranking</title><style>
*{box-sizing:border-box}body{margin:0;background:#0b1120;color:#e5e7eb;font-family:system-ui,-apple-system,sans-serif;overflow-x:hidden}.container{max-width:1180px;margin:auto;padding:14px}.header{background:#111827;border-bottom:1px solid #243244}.title{font-size:22px;font-weight:800}.status,small{color:#94a3b8;font-size:12px}.buttons{display:flex;gap:8px;overflow-x:auto;padding:12px 0 3px;white-space:nowrap}.btn{border:1px solid #334155;background:#111827;color:#cbd5e1;border-radius:9px;padding:9px 12px;font-weight:700;white-space:nowrap}.btn.active{background:#2563eb;color:#fff}.btn.gold{border-color:#b7791f;color:#fbbf24}.panel{background:#111827;border:1px solid #263448;border-radius:14px;padding:14px;margin:12px 0}.cards{display:grid;gap:10px}.card{background:#111827;border:1px solid #263448;border-radius:14px;padding:14px}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric label{display:block;color:#94a3b8;font-size:12px}.metric strong{display:block;margin-top:3px}.green{color:#34d399}.red{color:#fb7185}.muted{color:#94a3b8}.scroll{overflow-x:auto}.history{min-width:760px;width:100%;border-collapse:collapse}.history th,.history td{padding:9px 8px;border-bottom:1px solid #263448;text-align:right;white-space:nowrap}.history th:first-child,.history td:first-child{text-align:left}.missing{color:#fbbf24}.row-actions{display:flex;gap:8px;margin-top:10px}.input{background:#0f172a;color:#fff;border:1px solid #334155;border-radius:8px;padding:9px;width:100%}.hidden{display:none}.warn{color:#fbbf24}.error{color:#fb7185}@media(min-width:768px){.mobile-cards{display:none}}@media(max-width:767px){.container{padding:12px}.title{font-size:20px}}
</style></head><body><header class='header'><div class='container'><div class='title'>Binance Futures OI Ranking</div><div id='status' class='status'>加载中...</div><div id='collectorStatus' class='status'></div><div class='buttons' id='nav'></div></div></header><main class='container'><div id='view'></div></main><script>
const periods=['4H','12H','24H','48H','72H','96H'];let view='rank';let current='4H';const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pct=v=>v==null?'—':(v>=0?'+':'')+Number(v).toFixed(2)+'%';const num=v=>v==null?'N/A':(Math.abs(v)>=1e9?(v/1e9).toFixed(2)+'B':Math.abs(v)>=1e6?(v/1e6).toFixed(2)+'M':Math.abs(v)>=1e3?(v/1e3).toFixed(2)+'K':Number(v).toFixed(2));const price=v=>v==null?'N/A':(v<.001?Number(v).toFixed(8):v<1?Number(v).toFixed(6):Number(v).toFixed(4));
function nav(){document.getElementById('nav').innerHTML=`<button class='btn ${view==='watch'?'active':''}' onclick="showWatch()">⭐ 自选监控</button>`+periods.map(p=>`<button class='btn ${view==='rank'&&p===current?'active':''}' onclick="showRank('${p}')">${p}</button>`).join('')+`<button class='btn gold' onclick="showRank('金狗24H')">🔥 金狗24H</button><button class='btn gold' onclick="showRank('金狗48H')">🔥 金狗48H</button>`}
async function showWatch(){view='watch';nav();let r=await fetch('/api/watchlist');let j=await r.json();document.getElementById('view').innerHTML=`<div class='panel'><h2>⭐ 自选监控</h2><button class='btn' onclick="addSymbol()">+ 添加币种</button></div><div class='cards'>${j.data.length?j.data.map(x=>`<div class='card'><h3>${esc(x.symbol)}</h3><div class='metrics'><div class='metric'><label>当前价格</label><strong>${price(x.price)}</strong></div><div class='metric'><label>当前OI数量</label><strong>${num(x.oi_quantity)}</strong></div><div class='metric'><label>当前OI价值</label><strong>${num(x.oi_value)} USDT</strong></div><div class='metric'><label>最后数据</label><strong class='muted'>${x.timestamp||'N/A'}</strong></div></div><div class='row-actions'><button class='btn' onclick="detail('${x.symbol}')">查看详情</button><button class='btn' onclick="removeSymbol('${x.symbol}')">删除</button></div></div>`).join(''):'<div class="panel muted">暂无自选币种</div>'}</div>`}
async function addSymbol(){let s=prompt('输入币种，例如 LYNUSDT');if(!s)return;let r=await fetch('/api/watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:s})});let j=await r.json();if(!r.ok)alert(j.detail||'添加失败');else showWatch()}
async function removeSymbol(s){if(!confirm('确定删除 '+s+'？'))return;await fetch('/api/watchlist/'+s,{method:'DELETE'});showWatch()}
async function detail(s,h=72){let j=await fetch('/api/watchlist/'+s+'/history?hours='+h).then(r=>r.json());view='detail';nav();let current=j.data.find(x=>!x.missing);document.getElementById('view').innerHTML=`<button class='btn' onclick='showWatch()'>← 返回自选</button><div class='panel'><h2>${esc(s)}</h2><div class='metrics'><div class='metric'><label>当前价格</label><strong>${price(current?.price)}</strong></div><div class='metric'><label>当前持仓总数量</label><strong>${num(current?.oi_quantity)}</strong></div><div class='metric'><label>当前持仓总价值</label><strong>${num(current?.oi_value)} USDT</strong></div><div class='metric'><label>最后数据</label><strong class='muted'>${j.latest_timestamp||'N/A'}</strong></div></div></div><div class='panel'><h3>最近${h}小时</h3><div class='buttons'><button class='btn ${h===24?'active':''}' onclick="detail('${s}',24)">24H</button><button class='btn ${h===48?'active':''}' onclick="detail('${s}',48)">48H</button><button class='btn ${h===72?'active':''}' onclick="detail('${s}',72)">72H</button></div><div class='scroll'><table class='history'><thead><tr><th>时间</th><th>持仓数量</th><th>数量变化</th><th>持仓价值</th><th>价值变化</th><th>价格</th><th>价格变化</th></tr></thead><tbody>${j.data.map(x=>x.missing?`<tr class='missing'><td>${x.timestamp}</td><td colspan='6'>数据缺失</td></tr>`:`<tr><td>${x.timestamp.replace('T',' ').slice(5,16)}</td><td>${num(x.oi_quantity)}</td><td>${x.oi_quantity_change==null?'—':num(x.oi_quantity_change)}</td><td>${num(x.oi_value)} USDT</td><td>${x.oi_value_change==null?'—':num(x.oi_value_change)}</td><td>${price(x.price)}</td><td>${pct(x.price_change_percent)}</td></tr>`).join('')}</tbody></table></div></div>`}
async function showRank(p){view='rank';current=p;nav();let url=p.startsWith('金狗')?'/api/ranking/gold-dog?hours='+p.replace('金狗','').replace('H',''):'/api/ranking/'+p;let j=await fetch(url).then(r=>r.json());document.getElementById('view').innerHTML=`<div class='panel'><h2>排行榜 ${j.period||p}</h2><div class='status'>最新采集：${j.timestamp||'N/A'}</div></div><div class='cards mobile-cards'>${(j.data||[]).map((x,i)=>`<div class='card'><h3>${i+1}. ${x.symbol}</h3><div class='metrics'><div class='metric'><label>OI持仓数量涨幅</label><strong>${pct(x.oi_quantity_change_percent)}</strong></div><div class='metric'><label>OI持仓价值涨幅</label><strong>${pct(x.oi_value_change_percent)}</strong></div><div class='metric'><label>价格</label><strong>${price(x.price)}</strong></div><div class='metric'><label>价格变化</label><strong>${pct(x.price_change_percent)}</strong></div></div></div>`).join('')||'<div class="panel muted">该周期暂无完整数据</div>'}</div>`}
async function status(){try{let j=await fetch('/api/status').then(r=>r.json()),c=await fetch('/api/collector/status').then(r=>r.json());document.getElementById('status').textContent=j.latest_timestamp?'最新采集：'+new Date(j.latest_timestamp*1000).toLocaleString()+' | 合约：'+j.symbols:'尚未采集';document.getElementById('collectorStatus').textContent=(c.status==='COMPLETE'?'🟢':'🟡')+' 本小时：'+(c.success||0)+'/'+(c.expected||0)+' '+(c.status||'UNKNOWN')}catch(e){}}
nav();showRank('4H');status();</script></body></html>"""

def init(): init_db()
init()
