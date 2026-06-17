# 雏菊AIGC

一个面向商业化改造的文稿自然化与降重网站骨架。

## 已包含

- 粉白色小雏菊品牌标志
- 文本处理工作台，支持 TXT / DOCX / PDF 文本提取
- 三档服务：降低 AIGC 风险、降低重复率、AIGC + 降重
- 字数计费、余额扣费、充值支付单、订单和资金流水
- 邮箱注册、登录、退出和 Cookie 会话
- 注册时必须勾选用户协议和隐私政策，并记录协议接受时间
- 后台管理页，管理员登录后可查看用户、订单、充值和流水
- 后台余额调整，支持退款、异常扣费修正和客服补偿流水记录
- 后台上线检查，提示演示配置、支付、密钥、授权和数据库风险
- API Key 调用日志与每小时限流
- 关于我们、联系我们、隐私政策、用户协议、退款规则和免责声明页面
- FAQ、客服和 API 接入说明弹窗
- 前台客服工单提交，后台可查看、更新状态并导出
- 用户 API Key 创建、撤销和余额扣费调用
- AIGC 模式转发到本地 BypassAIGC
- 降重模式调用 OpenAI 兼容模型 API

## 启动

如果要同时启动 BypassAIGC 和雏菊AIGC，可以在工作区根目录运行：

```powershell
cd C:\Users\rog\Documents\智能化电商
.\start-daisy-stack.ps1
```

```powershell
cd C:\Users\rog\Documents\智能化电商\daisy-aigc
$env:PORT="9910"
$env:DAISY_DEFAULT_USER_ID="demo-user"
$env:DAISY_INITIAL_BALANCE_CENTS="3680"
$env:DAISY_REGISTRATION_BONUS_CENTS="0"
$env:DAISY_SESSION_TTL_SECONDS="1209600"
$env:DAISY_PAYMENT_WEBHOOK_SECRET="change-this-payment-secret"
$env:DAISY_PAYMENT_PROVIDER="mock"
$env:DAISY_ENABLE_MOCK_PAYMENT="1"
$env:DAISY_ADMIN_EMAILS="admin@daisy.local"
$env:DAISY_ADMIN_PASSWORD="change-this-admin-password"
$env:DAISY_API_RATE_LIMIT_PER_HOUR="60"
$env:DAISY_MAX_UPLOAD_BYTES="15728640"
$env:DAISY_MAX_EXTRACTED_CHARS="6000"
$env:DAISY_RESULT_RETENTION_DAYS="30"
$env:BYPASS_BASE_URL="http://localhost:9800/api"
$env:BYPASS_ADMIN_USERNAME="admin"
$env:BYPASS_ADMIN_PASSWORD="你的BypassAIGC后台密码"
$env:DAISY_BYPASS_COMMERCIAL_AUTHORIZED="0"
$env:OPENAI_API_KEY="你的模型API Key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:REWRITE_MODEL="deepseek-chat"
$env:DAISY_DEMO_FALLBACK="1"
python server.py
```

前台：

```text
http://localhost:9910
```

后台：

```text
http://localhost:9910/admin.html
```

默认本地演示管理员账号：

```text
admin@daisy.local / admin123456
```

正式部署前必须设置 `DAISY_ADMIN_PASSWORD`，并把默认密码改掉。

## 当前接口

- `GET /api/health`：健康检查
- `GET /api/public-config`：前端公开配置，包含支付演示开关
- `GET /api/me`：当前用户余额和登录态
- `POST /api/register`：邮箱注册，需提交 `accept_terms: true`
- `POST /api/login`：邮箱登录
- `POST /api/logout`：退出登录
- `GET /api/orders`：当前用户最近订单
- `GET /api/orders/{order_id}`：当前用户订单详情，已完成订单包含处理结果
- `GET /api/orders/{order_id}/download`：下载当前用户已完成订单的 TXT 结果
- `POST /api/orders/{order_id}/delete-result`：删除当前用户订单中保存的处理结果，保留订单和资金记录
- `GET /api/payments`：当前用户最近充值
- `GET /api/support-tickets`：当前登录用户最近工单
- `GET /api/api-keys`：当前用户 API Key 列表，需要登录
- `POST /api/api-keys/create`：创建 API Key，需要登录，明文密钥只返回一次
- `POST /api/api-keys/revoke`：撤销 API Key，需要登录
- `GET /api/admin/summary`：后台运营概览，需要管理员登录
- `POST /api/admin/adjust-balance`：管理员余额调整，需要管理员登录，参数 `{ "email": "...", "amount": 10, "note": "..." }`
- `POST /api/admin/cancel-payment`：管理员驳回或取消待支付单，需要管理员登录，参数 `{ "payment_id": "...", "note": "..." }`
- `POST /api/admin/update-ticket`：管理员更新客服工单状态，需要管理员登录
- `POST /api/admin/cleanup-results`：按 `DAISY_RESULT_RETENTION_DAYS` 清理过期订单结果文本，保留订单和资金记录
- `POST /api/payments/create`：创建充值支付单
- `POST /api/payments/notify-paid`：用户提交人工收款交易号或付款备注，等待后台核账
- `POST /api/support-tickets`：提交客服工单，支持支付、订单、退款、账号和其他问题
- `POST /api/payments/mock-confirm`：演示支付确认，仅在 `DAISY_ENABLE_MOCK_PAYMENT=1` 时可用
- `POST /api/payments/webhook`：支付平台回调入口
- `POST /api/recharge`：旧演示充值接口，仅在 `DAISY_ENABLE_MOCK_PAYMENT=1` 时可用
- `POST /api/extract-file`：上传并提取 TXT/DOCX/PDF 文本
- `POST /api/optimize`：提交处理，参数 `{ service, platform, language, text }`

API Key 调用示例：

```powershell
Invoke-RestMethod -Uri "http://localhost:9910/api/optimize" `
  -Method Post `
  -Headers @{ Authorization = "Bearer daisy_live_xxx" } `
  -ContentType "application/json" `
  -Body '{ "service": "repeat", "platform": "cnki", "language": "zh", "text": "待处理文本" }'
```

## 商用注意

BypassAIGC 当前开源许可证包含 NonCommercial 限制。把它作为商业服务上游使用前，需要先取得作者商业授权，或者替换为你自研且可商用的 AIGC 优化实现。

## 接入 BypassAIGC

雏菊AIGC 通过 HTTP API 调用本机 BypassAIGC，不复制它的代码。对接关系是：

- BypassAIGC 跑在 `http://localhost:9800`
- 雏菊AIGC 跑在 `http://localhost:9910`
- 雏菊 `.env` 中的 `BYPASS_BASE_URL=http://localhost:9800/api`
- 雏菊 `.env` 中的 `BYPASS_ADMIN_USERNAME` / `BYPASS_ADMIN_PASSWORD` 要和 `BypassAIGC/package/.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 一致
- BypassAIGC 的模型名建议使用实际可返回内容的模型，例如 `deepseek-chat`
- 测试真实链路时设置 `DAISY_DEMO_FALLBACK=0`，避免上游失败时返回演示结果

## 使用 .env 配置

推荐把 `.env.example` 复制为 `.env`，然后在 `.env` 里填写端口、支付密钥、管理员密码、BypassAIGC 地址和模型 API Key。服务启动时会自动读取 `.env`；如果同名系统环境变量已经存在，会优先使用系统环境变量。

```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
.\start.ps1
```

生产环境至少要确认这些值：

- `DAISY_DEMO_FALLBACK=0`
- `DAISY_ENABLE_MOCK_PAYMENT=0`
- `DAISY_PAYMENT_PROVIDER` 使用真实渠道
- `DAISY_PAYMENT_WEBHOOK_SECRET` 使用长随机密钥
- `DAISY_ADMIN_EMAILS` 和 `DAISY_ADMIN_PASSWORD` 已替换
- `DAISY_BYPASS_COMMERCIAL_AUTHORIZED=1`
- `OPENAI_API_KEY` 已填写
- `DAISY_RESULT_RETENTION_DAYS` 已设置为合理天数，例如 `30` 或 `90`

## 二维码人工收款

如果暂时还没有微信/支付宝官方接口，可以先用人工收款模式试运营：

```env
DAISY_PAYMENT_PROVIDER=manual_qr
DAISY_ENABLE_MOCK_PAYMENT=0
DAISY_MANUAL_PAYMENT_QR_URL=/pay/wechat-or-alipay.png
DAISY_MANUAL_PAYMENT_ACCOUNT=微信/支付宝收款名：你的主体名称
DAISY_MANUAL_PAYMENT_INSTRUCTIONS=请扫码支付对应金额，并在付款备注中填写支付单号。付款后等待客服确认入账。
```

用户创建支付单后，前台会展示收款码、付款备注和收款账户。用户付款后可提交交易号或补充备注，并在充值弹窗里查看最近充值的待支付、待核账、已到账和已驳回状态；你在后台核对真实收款流水后，用“支付入账”手动确认，系统才会给用户增加余额。若未收到对应款项或用户填错凭证，可在后台点击“驳回”，驳回原因会进入审计记录并展示给用户。

## 上线前清单

- 取得 BypassAIGC 商业授权，或替换为自研可商用服务
- 取得授权后设置 `DAISY_BYPASS_COMMERCIAL_AUTHORIZED=1`
- 将 `DAISY_DEMO_FALLBACK` 设为 `0`
- 将 `DAISY_ENABLE_MOCK_PAYMENT` 设为 `0`
- 修改 `DAISY_ADMIN_PASSWORD` 和 `DAISY_PAYMENT_WEBHOOK_SECRET`
- 接入真实微信/支付宝支付，并关闭 `mock-confirm`
- 增加验证码、登录限流、找回密码、风控和日志脱敏
- 将 SQLite 换成 PostgreSQL/MySQL，或至少做好定时备份
- 为上传文件增加对象存储、过期删除和病毒扫描
- 用户可在订单中心删除已保存的处理结果；正式运营建议设置 `DAISY_RESULT_RETENTION_DAYS`，并在后台定期执行旧结果清理
- 补齐隐私政策、用户协议、退款规则和客服入口
- 将合规页面中的演示邮箱、主体信息、备案号和退款条款替换为真实运营信息
- 后台余额调整属于高风险操作，正式上线前建议增加二次确认、操作人实名、IP 记录和更完整审计日志
- 根据套餐设置 `DAISY_API_RATE_LIMIT_PER_HOUR`，并定期审查后台“最近 API 调用”

## Supabase 邮箱登录与静态部署

微信扫码登录已不作为前台登录方式。邮箱注册、邮箱登录、找回密码、重置密码和用户中心页面使用 Supabase Auth，页面路径为：

- `/register`
- `/login`
- `/forgot-password`
- `/reset-password`
- `/dashboard`

部署到 Vercel/Netlify 的环境变量、Supabase Redirect URLs 和构建步骤见 [SUPABASE_DEPLOYMENT.md](./SUPABASE_DEPLOYMENT.md)。
