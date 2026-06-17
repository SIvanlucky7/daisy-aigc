# 雏菊AIGC 商用上线清单

这份清单用于把本地演示站切到真实收费服务。未完成前，不建议公开收款，也不要承诺检测结果。

## 必填配置

推荐复制 `.env.example` 为 `.env` 后集中填写配置；服务会在启动时自动读取 `.env`，但同名系统环境变量优先级更高。

```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
.\start.ps1
```

```powershell
$env:PORT="9910"
$env:DAISY_DEMO_FALLBACK="0"
$env:DAISY_ENABLE_MOCK_PAYMENT="0"
$env:DAISY_PAYMENT_PROVIDER="wechatpay-or-alipay"
$env:DAISY_PAYMENT_WEBHOOK_SECRET="replace-with-long-random-secret"
$env:DAISY_MANUAL_PAYMENT_QR_URL="/pay/wechat-or-alipay.png"
$env:DAISY_MANUAL_PAYMENT_ACCOUNT="微信/支付宝收款名：你的主体名称"
$env:DAISY_MANUAL_PAYMENT_INSTRUCTIONS="请扫码支付对应金额，并在付款备注中填写支付单号。付款后等待客服确认入账。"
$env:DAISY_ADMIN_EMAILS="ops@example.com"
$env:DAISY_ADMIN_PASSWORD="replace-with-strong-password"
$env:DAISY_API_RATE_LIMIT_PER_HOUR="60"
$env:DAISY_LOGIN_MAX_FAILED_ATTEMPTS="8"
$env:DAISY_LOGIN_LOCK_SECONDS="900"
$env:DAISY_RESULT_RETENTION_DAYS="30"
$env:BYPASS_BASE_URL="http://localhost:9800/api"
$env:BYPASS_ADMIN_USERNAME="admin"
$env:BYPASS_ADMIN_PASSWORD="replace-with-bypass-password"
$env:DAISY_BYPASS_COMMERCIAL_AUTHORIZED="1"
$env:OPENAI_API_KEY="replace-with-model-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:REWRITE_MODEL="deepseek-chat"
```

## 必须确认

- BypassAIGC 已获得商业授权，或已经替换为自研且可商用的 AIGC 优化服务。
- 真实支付渠道已接入，支付平台回调只调用 `/api/payments/webhook`，并使用 `DAISY_PAYMENT_WEBHOOK_SECRET` 生成签名。
- 若使用 `DAISY_PAYMENT_PROVIDER=manual_qr` 试运营，前台会展示收款码和支付单号，用户付款后可提交交易号或备注，必须由管理员核对真实流水后在后台人工确认入账；未收到款项或凭证错误时应在后台驳回待支付单并填写原因；规模化后建议替换为官方支付回调。
- 支付平台异常、线下转账或二维码收款，可由管理员在后台“支付入账”中人工确认，必须填写真实交易号和备注。
- 后台“上线检查”中阻塞项清零，再开放真实收费。
- 管理员默认邮箱和默认密码已替换，后台余额调整和支付入账都会进入“管理员审计”表。
- 登录限流已开启，防止用户账号和后台账号被撞库。
- 用户可在前台“账号安全”中自行修改密码，改密后其他会话会失效。
- API Key 限流已按套餐设置，后台定期检查“最近 API 调用”。
- 后台“数据导出”可下载订单、支付、资金流水、API 调用、管理员审计和用户清单。
- 后台“最近工单”需要安排客服定期处理，退款、支付异常和处理结果争议都应留在工单和审计记录中。
- 后台“数据库备份”可下载完整 SQLite 快照，用于灾备、迁移和离线留存。
- 用户可在订单中心删除已保存的处理结果；正式运营应设置 `DAISY_RESULT_RETENTION_DAYS`，定期在后台执行旧结果清理，并补充备份清理和用户数据删除响应流程。
- 隐私政策、用户协议、退款规则、免责声明、客服邮箱、企业主体、备案信息已换成真实信息。
- 新用户注册必须勾选用户协议和隐私政策，`users.terms_accepted_at` 会记录接受时间；正式上线前应确认协议版本和页面内容为真实有效版本。
- 不能宣传“保证过检测”“保证降到某个百分比”，只能描述为表达优化和降风险辅助工具。

## 验证命令

```powershell
py -3 -m py_compile .\server.py
$node="C:\Users\rog\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
& $node --check .\app.js
& $node --check .\admin.js
& $node --check .\icons.js
```

## 运营建议

- 每天导出订单、支付、资金流水、API 调用和管理员审计记录。
- 每天下载或自动保存 SQLite 完整备份；正式多用户运营建议迁移到 PostgreSQL 或 MySQL。
- 文件上传正式开放前，增加对象存储、过期删除、病毒扫描和内容安全审核。
- 对高风险后台操作增加二次确认、操作人实名、IP 白名单或双因素认证。
- 人工支付入账必须和真实收款流水逐笔核对，保留支付平台截图或对账单。
