# Supabase 邮箱登录与静态部署说明

## 技术栈识别

当前项目不是 Next.js、Vite 或 Vue。主站是普通 HTML/CSS/JS，后端是 Python 标准库 HTTP 服务，数据存储是 SQLite。

因此：

- 本地完整业务：运行 `python server.py`，可使用订单、充值、文件上传、AIGC 处理等 Python API。
- Vercel/Netlify 临时域名：可托管前端静态页面和 Supabase 邮箱注册登录。若要让订单/充值/文本处理也在线可用，还需要把 Python API 部署到支持长驻后端的服务，例如 Render、Railway、Fly.io、云服务器等，再把前端 API 地址改成该后端地址。

## Supabase 后台配置

进入 Supabase 项目后台：

1. 打开 `Authentication`。
2. 打开 `Providers`。
3. 启用 `Email` Provider。
4. 确认邮箱密码登录开启。
5. 打开 `URL Configuration`。
6. `Site URL` 本地开发可先填：

```text
http://localhost:9910
```

如果部署到 Vercel 或 Netlify，再改成线上临时域名，例如：

```text
https://你的项目名.vercel.app
https://你的项目名.netlify.app
```

7. `Redirect URLs` 至少加入：

```text
http://localhost:9910/**
http://localhost:3000/**
http://localhost:5173/**
https://你的项目名.vercel.app/**
https://你的项目名.netlify.app/**
```

## 环境变量

本地 `.env`：

```env
VITE_SUPABASE_URL=https://your-project-ref.supabase.co
VITE_SUPABASE_ANON_KEY=your-public-anon-key
NEXT_PUBLIC_SUPABASE_URL=https://your-project-ref.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-public-anon-key
```

Vercel/Netlify 项目环境变量：

```text
VITE_SUPABASE_URL
VITE_SUPABASE_ANON_KEY
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY
```

只填写 Supabase 的 public anon key，不要填写 service_role key。

## 本地运行

```powershell
cd C:\Users\rog\Documents\智能化电商\daisy-aigc
python server.py
```

访问：

```text
http://localhost:9910
http://localhost:9910/register
http://localhost:9910/login
http://localhost:9910/forgot-password
http://localhost:9910/reset-password
http://localhost:9910/dashboard
```

## 静态构建

```powershell
cd C:\Users\rog\Documents\智能化电商\daisy-aigc
npm install
npm run build
```

构建产物在：

```text
dist/
```

## Vercel 部署

1. 将 `daisy-aigc` 推送到 GitHub。
2. 在 Vercel 导入仓库。
3. Framework Preset 选择 `Other`。
4. Build Command 填：

```text
npm run build
```

5. Output Directory 填：

```text
dist
```

6. 添加上面的 Supabase 环境变量。
7. 部署完成后，把 Vercel 临时域名加入 Supabase `Site URL` 和 `Redirect URLs`。

## Netlify 部署

1. 将 `daisy-aigc` 推送到 GitHub。
2. 在 Netlify 导入仓库。
3. Build command 填：

```text
npm run build
```

4. Publish directory 填：

```text
dist
```

5. 添加上面的 Supabase 环境变量。
6. 部署完成后，把 Netlify 临时域名加入 Supabase `Site URL` 和 `Redirect URLs`。

## 上线测试

1. 打开 `/register`，用邮箱注册。
2. 检查邮箱验证邮件。
3. 打开 `/login`，用邮箱密码登录。
4. 登录后确认跳转 `/dashboard`。
5. 刷新 `/dashboard`，确认仍保持登录。
6. 点击退出登录，确认回到登录页。
7. 打开 `/forgot-password`，发送重置邮件。
8. 从邮件进入 `/reset-password`，设置新密码。
