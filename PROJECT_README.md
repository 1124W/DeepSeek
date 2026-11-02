# DeepSeek 量化策略 · 香港服务器部署指南
> 本目录是 **香港直连版本**（已移除本地 10808 代理配置），上传 `/opt/deepseek` 即可使用。

## 📦 目录结构
```
deploy_server/
├── main.py                    # 数据抓取 + AI 预测(已移除代理)
├── dashboard_final.py         # Flask 看板（分页每页 30 条）
├── requirements.txt
├── strategy_data.csv          # 历史数据，不传也行，但第一次运行会空
├── ai_lessons.txt             # AI 自学习教训库
├── start.sh                   # 方案A：nohup 一键启动（最简单）
├── systemd/                   # 方案B：systemd 开机自启 + 自动重启（强烈推荐）
│   ├── deepseek-dashboard.service
│   └── deepseek-fetcher.service
└── README.md                  # 本文件
```

## ⚙️ 一次性环境准备（Ubuntu 20.04/22.04）
```bash
# 1. 基础依赖（Python 3.9+ 推荐）
sudo apt update
sudo apt install -y python3 python3-pip python3-venv curl

# 2. 上传代码到服务器（本地执行以下命令之一，看你习惯）
# 方式 A：scp
#   scp -r ./deploy_server/* user@YOUR_SERVER_IP:/opt/deepseek/
# 方式 B：先 git push 再服务器拉
# 方式 C：用宝塔 / WinSCP 上传整个 deploy_server 文件夹内容到 /opt/deepseek

ssh user@YOUR_SERVER_IP
sudo mkdir -p /opt/deepseek
sudo chown -R $USER:$USER /opt/deepseek   # 或改成你要运行的用户
cd /opt/deepseek

# 3. (强烈建议) 虚拟环境
python3 -m venv venv
source venv/bin/activate

# 4. 安装依赖
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 🚀 方案 A：nohup 一键启动（最简单，适合临时跑）
```bash
cd /opt/deepseek
chmod +x start.sh

# 用虚拟环境时，先指定 PY_BIN 再启动
export PY_BIN="/opt/deepseek/venv/bin/python"
./start.sh
# 或者一行:   PY_BIN=/opt/deepseek/venv/bin/python ./start.sh

# 查看状态
./start.sh status
# 停止
./start.sh stop
```

### 常用运维命令
```bash
tail -f logs/main.log                       # 数据抓取实时日志
tail -f logs/dashboard_error.log            # 看板错误日志
tail -100 logs/main.log | grep -i error     # 抓取脚本报错
```

### 端口放行（否则外网打不开）
- **阿里云/腾讯云**: 控制台 → 安全组 → 放行 `TCP 8002`
- **Ubuntu ufw**（若开启）:
  ```bash
  sudo ufw allow 8002/tcp
  ```

然后访问 `http://你的服务器IP:8002` 即可看到看板。

---

## 🔥 方案 B：systemd 开机自启 + 崩溃自动重启（生产环境推荐）
### 1. 安装 service 文件
```bash
cd /opt/deepseek
sudo cp systemd/*.service /etc/systemd/system/

# ⚠️ 如果路径不是 /opt/deepseek，要先手动改 service 文件里的 WorkingDirectory 和路径。
# ⚠️ 用 venv 的话，改 service 里的 ExecStart 开头为
#   /opt/deepseek/venv/bin/python ...
```

### 2. 启动 & 设为开机自启
```bash
sudo systemctl daemon-reload

# 开机自启
sudo systemctl enable deepseek-fetcher.service
sudo systemctl enable deepseek-dashboard.service

# 立即启动
sudo systemctl start  deepseek-fetcher.service
sudo systemctl start  deepseek-dashboard.service
```

### 3. 查看状态 & 日志
```bash
# 运行状态
systemctl status deepseek-fetcher deepseek-dashboard

# 抓取实时日志（最常用）
journalctl -u deepseek-fetcher.service -f -n 100
# 看板实时日志
journalctl -u deepseek-dashboard.service -f -n 100

# 重启 / 停止
sudo systemctl restart deepseek-fetcher
sudo systemctl stop    deepseek-fetcher
```

### 4. 端口放行：同方案 A

---

## 🎯 用 Nginx / NPM 反代（可选，上域名 + HTTPS）
如果你要用 `https://quant.example.com` 而不是 IP 访问：

### 宝塔面板 / Nginx 直接配置（示例）
```nginx
server {
    listen 80;
    server_name quant.example.com;

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }
}
```
然后宝塔里一键申请 SSL (Let's Encrypt) 就搞定 HTTPS。

### Nginx Proxy Manager
- 源站地址填服务器内网/本机 `127.0.0.1:8002`，Scheme 选 `http`
- 勾上 "Block Common Exploits" + "Websockets Support"
- 申请 SSL 证书 → 完成

---

## 🧩 默认值速查
| 项 | 默认值 | 改哪里 |
|---|---|---|
| 看板端口 | **8002** | `start.sh` 里的 `DASH_PORT` 或 `.service` 的 `--bind 0.0.0.0:8002` |
| Gunicorn workers | 3 | `WORKERS` / `--workers` |
| DeepSeek API Key | `<已脱敏，请填写你自己的 Key>` | `main.py` 顶部 `DEEPSEEK_API_KEY` |
| 抓取间隔 | 每 10 分钟一轮（对齐整 10 分钟） | `main.py` 最底部 `wait_sec` |
| 交易币种 | BTC/USDT、ETH/USDT | `main.py` 顶部 `TARGET_COINS` |

---

## ⚠️ 常见坑
1. **Permission denied 写 CSV**: 确保运行 systemd 的 User 对 `/opt/deepseek` 有写权限，或者把 `strategy_data.csv` `chmod 666`。
2. **看板里一直显示"正在加载数据"**: 先访问 `http://IP:8002/api/data` 看接口有没有报错；大概率是 strategy_data.csv 不存在或目录没权限读。
3. **价格显示比币安现货网页略低**：这是正常的 —— 当前脚本抓的是 **币安 U 本位合约(fapi)** 价格，合约和现货本来就有基差。若需要完全对齐现货，把 `binanceusdm` 换成 `binance` 即可。
4. **改了代码没生效**: 用 nohup 就 `./start.sh && ./start.sh`（它会先停再启），用 systemd 就 `sudo systemctl restart deepseek-fetcher deepseek-dashboard`。
