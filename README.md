# 艾宾浩斯遗忘曲线 · 本地复习 Web


该项目结合艾宾浩斯遗忘曲线和LLM生成复习题、面试题以提高学习效率。

本地应用：录入知识点（文本 / 图片 / 文件 / 代码 / 文件夹）→ 大模型自动出题 → 按艾宾浩斯曲线提醒复习 → 先答题再学习。

## 快速开始

```bash
# 1. 进入项目目录
cd review

# 2. 创建虚拟环境（推荐 Python 3.9+）
py -3.9 -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
# 若 HTTPS 镜像 SSL 失败，可用：
# pip install -r requirements.txt -i http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 4. 配置 API Key
copy .env.example .env
# 编辑 .env，填写 LLM_API_KEY，并按需修改 LLM_BASE_URL / LLM_MODEL

# 5. 启动（Web + 桌面 Toast 提醒）
python run.py
```

浏览器打开：http://127.0.0.1:8765/

禁用桌面提醒：

```bash
python run.py --no-notify
```

## API 配置


| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_API_KEY` | API 密钥 | `crsr_...` 或 `sk-...` |
| `LLM_BASE_URL` | 接口根路径（OpenAI 兼容时用） | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名 | `deepseek-v4-flash` / `auto`（Cursor） |

也可在网页「设置」中修改 Base URL、模型与 Key。

### 联网搜索（可选）

设置页可开启：

- **出题时联网搜索**：用 DuckDuckGo 检索标题相关网页摘要，并入出题材料（冲突时以本地知识点为准）
- **判分时联网搜索**：仅简答/场景题会再检索（更慢，默认关闭）

## 复习规则

间隔：`1 → 2 → 4 → 7 → 15 → 30` 天

- 答对：进入下一档
- 答错：重置为第 1 档（次日再复习），并先展示知识点材料，确认学习后再回到今日待复习列表

## 提醒方式

1. **打开网页**：首页列出今日待复习；角标显示数量  
2. **浏览器通知**：设置中开启并授权后，到设定时刻若有待复习会弹通知（当天一次）  
3. **桌面 Toast**：`python run.py` 常驻时，到点且有待复习会弹 Windows 通知  

> 浏览器完全退出且本机服务未启动时无法提醒，请保持 `run.py` 运行。

## 换电脑迁移

复制整个项目目录（至少包含）：

- `data/`（数据库 `app.db` + `uploads/` 附件）
- `.env`（API 配置）
- 可选：`data/settings.json`

在新电脑安装依赖后执行 `python run.py`，知识点与复习进度不变。

## 目录结构

```
review/
  app/           # 后端与静态前端
  data/          # 运行时数据（可迁移）
  run.py         # 启动入口
  requirements.txt
  .env           # 本地密钥（勿提交公开仓库）
```
