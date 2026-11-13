# Mumu Desktop

Windows 本地桌面端，技术栈为 Electron、Vue 3、FastAPI、Playwright、browser-use 和 SQLite。

完整安装、模型配置和使用说明见项目根目录 [README.md](../README.md)。

## 开发启动

```powershell
npm install
npm run dev
```

`npm run dev` 会同时启动 Vite 和应用内嵌 FastAPI。只启动前端预览：

```powershell
npm run web
```

## 打包

```powershell
npm run build:win
```

输出目录为 `release/`。打包前需要安装 `backend/requirements.txt` 中的依赖。
