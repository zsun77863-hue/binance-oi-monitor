from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from database import init_db, get_connection
from config import RANKING_LIMIT, APP_TIMEZONE
from datetime import datetime
from zoneinfo import ZoneInfo

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

HTML="""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1.0, maximum-scale=1.0'><title>Binance Futures OI Ranking</title><style>
*{box-sizing:border-box}html,body{width:100%;margin:0;padding:0}body{overflow-x:hidden;background:#0b1120;color:#e5e7eb;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.container{width:100%;max-width:1180px;margin:0 auto;padding:16px}.header{background:linear-gradient(180deg,#111827,#0f172a);border-bottom:1px solid #243244}.title{font-size:22px;font-weight:800}.status{margin-top:7px;color:#94a3b8;font-size:13px}.period-buttons{display:flex;gap:8px;overflow-x:auto;padding:14px 0 4px;scrollbar-width:none}.period-buttons::-webkit-scrollbar{display:none}.period-btn{flex:0 0 auto;min-width:56px;height:38px;border-radius:10px;border:1px solid #334155;background:#111827;color:#cbd5e1;font-size:14px;font-weight:700}.period-btn.active{background:#2563eb;color:#fff;border-color:#2563eb}.period-btn[data-period^='金狗']{border-color:#b7791f;color:#fbbf24}.period-btn[data-period^='金狗'].active{background:#7c2d12;border-color:#f59e0b;color:#fff}.section-title{font-size:17px;font-weight:750;margin:18px 0 10px}.loading,.error{padding:24px 8px;text-align:center;color:#94a3b8}.error{color:#fca5a5}.cards{display:grid;grid-template-columns:1fr;gap:10px}.card{background:#111827;border:1px solid #263448;border-radius:14px;padding:14px}.card-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}.rank{font-size:18px;font-weight:800;color:#fbbf24;min-width:26px}.symbol{font-weight:800;font-size:16px}.divergence{margin-left:auto;color:#fbbf24;font-size:11px;font-weight:800}.strong{color:#fb7185}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px}.metric label{display:block;color:#94a3b8;font-size:12px;margin-bottom:3px}.metric strong{font-size:15px}.green{color:#34d399}.red{color:#fb7185}table{display:none;width:100%;border-collapse:collapse}th,td{padding:11px 8px;border-bottom:1px solid #1e293b;text-align:right}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}th{color:#94a3b8;font-size:12px}.desktop-only{display:none}@media(min-width:768px){.container{padding:24px}.cards{display:none}table{display:table}.desktop-only{display:block}.title{font-size:25px}}@media(max-width:767px){.container{padding:12px}.header .container{padding-top:14px;padding-bottom:10px}}
</style></head><body><header class='header'><div class='container'><div class='title'>Binance Futures OI Ranking</div><div id='status' class='status'>正在加载...</div><div id='collectorStatus' class='status'>采集完整性加载中...</div><div id='periodButtons' class='period-buttons'></div></div></header><main class='container'><div class='section-title'>排行榜 <span id='periodLabel' class='status'></span></div><div id='message' class='loading'>加载中...</div><div id='cards' class='cards'></div><table class='desktop-only'><thead><tr><th>#</th><th>币种</th><th>当前OI</th><th>OI增幅</th><th>OI增加</th><th>价格</th><th>价格变化</th></tr></thead><tbody id='tbody'></tbody></table></main><script>
const periods=['4H','12H','24H','48H','72H','96H','金狗24H','金狗48H'];let currentPeriod='4H';const apiPeriod=p=>p.startsWith('金狗')?'/api/ranking/gold-dog?hours='+p.replace('金狗','').replace('H',''): '/api/ranking/'+encodeURIComponent(p);
const money=v=>{if(v==null)return 'N/A';let a=Math.abs(v);return '$'+(a>=1e9?(v/1e9).toFixed(2)+'B':a>=1e6?(v/1e6).toFixed(2)+'M':a>=1e3?(v/1e3).toFixed(2)+'K':v.toFixed(2))};const compact=v=>{let a=Math.abs(v);return a>=1e9?(v/1e9).toFixed(2)+'B':a>=1e6?(v/1e6).toFixed(2)+'M':a>=1e3?(v/1e3).toFixed(2)+'K':v.toFixed(0)};const pct=v=>v==null?'N/A':(v>=0?'+':'')+v.toFixed(2)+'%';const price=v=>v<.001?v.toFixed(8):v<1?v.toFixed(6):v<100?v.toFixed(4):v.toFixed(2);const cls=v=>v>=0?'green':'red';
function renderPeriodButtons(){document.getElementById('periodButtons').innerHTML=periods.map(p=>`<button class="period-btn ${p===currentPeriod?'active':''}" data-period="${p}">${p.startsWith('金狗')?'🔥 '+p:p}</button>`).join('');document.querySelectorAll('.period-btn').forEach(b=>b.addEventListener('click',()=>switchPeriod(b.dataset.period)))}
function card(x,i){let flag=x.divergence==='strong'?'<span class="divergence strong">🔥 强烈背离</span>':x.divergence==='flat'?'<span class="divergence">⚠ OI增加 / 价格横盘</span>':x.divergence==='sync'?'<span class="divergence green">📈 OI + 价格同步上涨</span>':'';return `<article class="card"><div class="card-head"><span class="rank">${i<3?['🥇','🥈','🥉'][i]:i+1}</span><span class="symbol">${x.symbol}</span>${flag}</div><div class="metrics"><div class="metric"><label>OI持仓数量｜涨跌幅</label><strong class="${cls(x.oi_change_percent)}">${pct(x.oi_change_percent)}</strong><small>增加 ${compact(x.oi_change_amount)}</small></div><div class="metric"><label>OI持仓价值｜涨跌幅</label><strong class="${cls(x.oi_change_usd_percent)}">${pct(x.oi_change_usd_percent)}</strong><small>增加 ${money(x.oi_change_usd_amount)}</small></div><div class="metric wide"><label>持仓数量（最低 → 最高 → 当前）</label><strong>${compact(x.min_oi)} → ${compact(x.max_oi)} → ${compact(x.current_oi)}</strong></div><div class="metric wide"><label>持仓价值（最低 → 最高 → 当前）</label><strong>${money(x.min_oi_usd)} → ${money(x.max_oi_usd)} → ${money(x.current_oi_usd)}</strong></div><div class="metric"><label>价格</label><strong>${price(x.current_price)}</strong></div><div class="metric"><label>价格变化</label><strong class="${cls(x.price_change_percent)}">${pct(x.price_change_percent)}</strong></div></div></article>`}
function row(x,i){return `<tr><td>${i+1}</td><td>${x.symbol}</td><td>${money(x.current_oi_usd)}</td><td class="${cls(x.oi_change_usd_percent)}">${pct(x.oi_change_usd_percent)}</td><td class="${cls(x.oi_change_usd_amount)}">${money(x.oi_change_usd_amount)}</td><td>${price(x.current_price)}</td><td class="${cls(x.price_change_percent)}">${pct(x.price_change_percent)}</td></tr>`}
async function loadRanking(period){currentPeriod=period;renderPeriodButtons();document.getElementById('periodLabel').textContent=period;document.getElementById('message').className='loading';document.getElementById('message').textContent='加载中...';document.getElementById('cards').innerHTML='';document.getElementById('tbody').innerHTML='';try{let r=await fetch(apiPeriod(period));if(!r.ok)throw Error('HTTP '+r.status);let j=await r.json();if(j.error)throw Error(j.error);if(j.strategy){document.getElementById('periodLabel').textContent=j.period+'｜'+j.strategy+'｜符合条件：'+j.qualified_count+' 个'}if(!j.data.length){document.getElementById('message').textContent=j.message||'该周期暂无完整数据';return}document.getElementById('message').textContent='';document.getElementById('cards').innerHTML=j.data.map(card).join('');document.getElementById('tbody').innerHTML=j.data.map(row).join('')}catch(e){document.getElementById('message').className='error';document.getElementById('message').textContent='加载失败：'+e.message}}
async function loadStatus(){try{let j=await fetch('/api/status').then(r=>r.json());let c=await fetch('/api/collector/status').then(r=>r.json());let icon=c.status==='COMPLETE'?'🟢':c.status==='PARTIAL'?'🟡':c.status==='FAILED'?'🔴':'⚪';document.getElementById('collectorStatus').textContent=icon+' 本小时数据：'+(c.success||0)+'/'+(c.expected||0)+' | 状态：'+(c.status||'UNKNOWN')+(c.rate_limit_events?' | 限流事件：'+c.rate_limit_events:'');document.getElementById('status').textContent=j.latest_timestamp?'最新采集：'+new Date(j.latest_timestamp*1000).toLocaleString()+' | 合约：'+j.symbols:'尚未采集数据'}catch(e){document.getElementById('status').textContent='状态获取失败'}}
function switchPeriod(p){loadRanking(p)}renderPeriodButtons();loadRanking('4H');loadStatus();setInterval(()=>loadRanking(currentPeriod),60000);setInterval(loadStatus,60000);
</script></body></html>"""

def init(): init_db()
init()
