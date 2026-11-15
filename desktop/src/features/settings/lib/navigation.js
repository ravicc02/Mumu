export const settingsNavigation = [
  { id: "common", label: "常用", group: "常用", keywords: "开机自启 登录启动 自动检查更新 启动 常规" },
  { id: "account", label: "账户", group: "账户", keywords: "头像 昵称 邮箱 本地资料" },
  { id: "model", label: "模型配置", group: "AI 与任务", keywords: "供应商 API Key 接口 地址 思考强度 主模型 备用模型" },
  { id: "assignments", label: "作业配置", group: "AI 与任务", keywords: "最终提交 自动评测 最大步骤 并发" },
  { id: "learning", label: "网课配置", group: "AI 与任务", keywords: "章节题目 自动提交 作业答题 播放 倍速 音量 后台运行 无头浏览器 资源 等待 核验" },
  { id: "attendance", label: "签到域名", group: "AI 与任务", keywords: "签到 扫码 链接分组 域名 中继 服务器 HTTPS" },
  { id: "system", label: "系统设置", group: "系统", keywords: "数据目录 日志 保留 空间 存储 采集 并发" },
  { id: "about", label: "关于", group: "系统", keywords: "版本 更新 检查更新 项目 主页 许可证 GPL 开源 日志目录" },
];
export function filterSettingsNavigation(query) {
  const needle = String(query || "").replace(/\s+/g, "").toLocaleLowerCase();
  return settingsNavigation.filter(section => !needle || (section.label + " " + section.keywords).replace(/\s+/g, "").toLocaleLowerCase().includes(needle));
}
