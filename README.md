# 量化策略项目备份 + 回测审计看板

> ⚠️ **安全提示**：本包内的 `main.py` 和 `PROJECT_README.md` 已做脱敏处理，DeepSeek API Key 被替换为
> `os.environ.get("DEEPSEEK_API_KEY", "在此填入你的 DeepSeek API Key")`。
> 如果你要上传到**公开**仓库，请务必保持脱敏；真实带 Key 的版本只放在服务器或本地。

## 目录说明

| 文件 | 说明 |
|---|---|
| `index.html` | **回测审计看板**（单文件、离线可跑，数据已内嵌）——GitHub Pages 展示的就是它 |
| `main.py` | 预测/采集主程序（已脱敏），每 10 分钟跑一轮，调 DeepSeek 生成多空预测 |
| `dashboard_final.py` | 线上看板程序（Gunicorn，端口 8002） |
| `start.sh` / `requirements.txt` | 启动脚本与依赖 |
| `strategy_data.csv` | 预测数据库，约 3.2 万条（BTC/USDT、ETH/USDT） |
| `ai_lessons.txt` | AI 自学习教训日志 |
| `systemd/` | 两个 systemd 服务单元 |
| `logs/` | 看板错误日志 |
| `tools/` | 审计看板生成工具：`_build_dashboard.py`（读 CSV 生成 index.html）、`_template.html`、`_analyze.py` |
| `PROJECT_README.md` | 原项目说明（已脱敏） |

## GitHub 上传步骤（网页操作，不用命令行）

1. 登录 GitHub → 右上角 `+` → **New repository**
2. 填仓库名，例如 `quant-strategy-backup`
   - 选 **Public**（免费账户的 GitHub Pages 需要公开仓库；如果只想备份不想展示，选 Private，就没法开 Pages）
   - **不要**勾选 "Add a README file" / .gitignore / License —— 保持空仓库
   - 点 **Create repository**
3. 空仓库页面中间有 **"uploading an existing file"**，点它（或直接把 `publish` 文件夹里的文件拖进页面）
   - 建议先拖 `index.html` + `README.md` + `main.py` 等小文件
   - 大文件（`ai_lessons.txt` 15MB、`strategy_data.csv` 6.9MB）可以第二批拖，网页单文件上限 25MB，都在范围内
   - 提交说明随便写，例如 `initial backup` → **Commit changes**
4. 想开 Pages：仓库 **Settings → Pages → Source** 选 `Deploy from a branch`，分支 `main`、目录 `/(root)` → **Save**
5. 等 1-2 分钟，访问 `https://<你的GitHub用户名>.github.io/<仓库名>/` 就能看到看板

## 命令行方式（可选）

```bash
cd /d/code/lianghua_20261030/publish
git remote add origin https://github.com/<用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

推送时会弹登录：用户名填 GitHub 账号；**密码处填 Personal Access Token**（GitHub → Settings → Developer settings → Personal access tokens → Fine-grained token，权限勾 `Contents: Read and write`），不是登录密码。

## 数据更新后同步

```bash
cd /d/code/lianghua_20261030
python _build_dashboard.py                 # 用新的 strategy_data.csv 重新生成看板
cp 回测审计台.html publish/index.html       # 覆盖发布包
cp strategy_data.csv publish/strategy_data.csv
cd publish && git add -A && git commit -m "update 数据" && git push
```

## 审计结论速览

- 严格口径（方向一致才算赢）：10 分钟胜率 **50.4%**、30 分钟 **50.1%** —— 与抛硬币无异
- main.py 自记胜率 58.3% / 58.9%，虚高主要来自「波动 <0.02%/0.04% 无论方向都算赢」的规则
- 判 ✅ 但真实方向相反的「假胜」约 2,700 单
- 唯一在多重比较校正后仍显著的切片：**03:00 时段 56.3%（z=4.53）**；14:00 为低洼 47.2%
