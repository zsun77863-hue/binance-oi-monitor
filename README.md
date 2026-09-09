# 币安 Binance Futures OI Monitor

这是一个 Binance USDT 永续合约持仓量（Open Interest，OI）监控与排行榜项目。项目每小时采集 Binance Futures 的合约 OI、标记价格和资金费率，并提供深色风格的网页排行榜与 JSON API。

## 功能

- 每小时采集 Binance USDT 永续合约。
- 使用本地 `data/symbols.json` 缓存交易对列表，避免每小时重复请求 `exchangeInfo`。
- OI 请求使用全局节流，默认间隔约 0.58 秒，528 个合约通常约 5 分钟完成。
- 429 和 418 使用 Retry-After、指数退避和冷却处理。
- 失败合约单独重试，部分成功数据仍然写入 SQLite。
- 使用文件锁避免同一时间运行多个 collector。
- 普通排行榜按 **OI 持仓数量涨幅** 降序排列，而不是 OI 价值涨幅。
- 支持 4H、12H、24H、48H、72H、96H，以及固定周期的金狗24H和金狗48H策略。
- 提供采集完整性 API，可区分 COMPLETE、PARTIAL 和 FAILED。

## 运行环境

- Python 3.10+
- SQLite
- Binance Futures REST API
- FastAPI、Uvicorn、Requests

## 本地安装

```bash
git clone https://github.com/<your-account>/币安.git
cd 币安
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

编辑 `config.py`，至少确认 `DB_PATH` 指向可写的 SQLite 路径，例如：

```python
DB_PATH = "./oi.db"
```

初始化数据库并启动网页服务：

```bash
python3 -c "from database import init_db; init_db()"
uvicorn app:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/`。

## 手动采集

```bash
python collector.py
```

首次运行或需要刷新交易对缓存时：

```bash
python collector.py --refresh-symbols
```

采集器会将交易对缓存写入 `data/symbols.json`。生产环境建议将 `DB_PATH` 改成绝对路径，并确保运行用户有写权限。

## systemd 每小时运行

将项目安装到 `/opt/oi-monitor` 后，可使用以下模板：

```bash
sudo cp deploy/oi-collector.service /etc/systemd/system/
sudo cp deploy/oi-collector.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oi-collector.timer
sudo systemctl start oi-collector.service
```

查看状态：

```bash
systemctl status oi-collector.timer
journalctl -u oi-collector.service -f
```

采集约每小时整点启动，528 个合约通常约 5 分钟完成。采集器内部使用 `/opt/oi-monitor/collector.lock` 防止重复运行；如果安装到其他目录，请同步修改 service 文件中的路径。

## API

```text
GET /api/status
GET /api/collector/status
GET /api/ranking/4H
GET /api/ranking/12H
GET /api/ranking/24H
GET /api/ranking/48H
GET /api/ranking/72H
GET /api/ranking/96H
GET /api/ranking/gold-dog?hours=24
GET /api/ranking/gold-dog?hours=48
```

普通榜单的主要排序字段是 `oi_quantity_change_percent`。金狗策略固定使用对应的 24H 或 48H 数据，筛选条件为 OI 数量涨幅大于 30%，且价格变化在 -30% 到 +30% 之间。

`/api/collector/status` 返回最近一次采集的预期数量、成功数量、失败数量、状态和限流事件，例如：

```json
{
  "expected": 528,
  "success": 528,
  "failed": 0,
  "status": "COMPLETE",
  "rate_limit_events": 0
}
```

## 生产部署注意事项

不要把 SQLite 数据库、虚拟环境、交易对缓存、SSH 密钥、Binance API 密钥或 GitHub Token 提交到公开仓库。本仓库的 `.gitignore` 已忽略这些运行时文件。当前采集接口使用公开 Binance Futures 数据，不需要 Binance API 密钥。

如果公开仓库曾经使用过个人访问令牌进行推送，请在 GitHub 中撤销该令牌并重新生成一个最小权限令牌。不要把令牌写入脚本、配置或 README。

## 项目文件

| 文件 | 作用 |
|---|---|
| `app.py` | FastAPI 网页、排行榜和 API |
| `collector.py` | 限速采集、缓存、重试和采集状态记录 |
| `database.py` | SQLite 连接与基础表初始化 |
| `config.py` | 时区、Binance 地址、数据库和排名配置 |
| `init_history.py` | 历史数据初始化工具 |
| `update_prices.py` | 历史价格回填工具 |
| `deploy/` | systemd 服务和定时器模板 |

## 许可

如需对外发布或商业使用，请根据你的实际使用场景补充许可证。当前仓库未默认声明开源许可证。
