# DESIGNS.md

> **通用设计规范 v1.0**
> 提炼自一个成熟商用 AI 桌面应用的设计系统（132 个 token / 3,166 条 CSS 规则 / 79 个动效关键帧），
> 去品牌化后整理为可直接落地的通用规范。
> 所有数值均为实测值，可直接使用。

---

## 目录

1. [设计哲学（5 条原则）](#1-设计哲学)
2. [Token 分层架构](#2-token-分层架构)
3. [色彩规范](#3-色彩规范)
4. [表面与层级](#4-表面与层级)
5. [形状规范](#5-形状规范)
6. [阴影规范](#6-阴影规范)
7. [字体与排版](#7-字体与排版)
8. [间距与栅格](#8-间距与栅格)
9. [动效规范](#9-动效规范)
10. [组件规范](#10-组件规范)
11. [状态与反馈](#11-状态与反馈)
12. [无障碍规范](#12-无障碍规范)
13. [主题化规范](#13-主题化规范)
14. [工程落地清单](#14-工程落地清单)
15. [反模式清单](#15-反模式清单)

---

## 1. 设计哲学

### 原则 1：设计写在 token 里，不写在 className 里

组件只消费**语义 token**，不写死具体值。

```diff
- <div class="bg-gray-100 text-gray-900 rounded-lg shadow-md border border-gray-200">
+ <div class="ds-card">
```

收益：换主题、改风格、适配无障碍，全部变成改几个变量，组件代码零改动。

### 原则 2：为暗色单独做色彩决策

暗色不是亮色的反色。同一语义色在两个模式下可以是**不同色相**。

```
亮色 品牌色  墨绿 #2d4739  （沉稳，适合暖白底）
暗色 品牌色  亮绿 #36d385  （发光，适合近黑底）
```

### 原则 3：连续曲率优于几何圆弧

圆角用超椭圆而非圆弧。人眼对曲率突变敏感，超椭圆（squircle）让曲率平滑过渡。

### 原则 4：物理光照优于单层投影

阴影用 5~7 层递减模拟真实光照，不用单层大模糊。

### 原则 5：无障碍是强制约束，不是建议

用户输入的任何颜色都必须过 WCAG AA（4.5:1）。动效必须可降级。焦点必须可见。

---

## 2. Token 分层架构

**三层，职责单一，只允许上层引用下层。**

```
┌─────────────────────────────────────────┐
│ L3 组件 token（组件专用，引用 L1/L2）      │
│    --ds-chat-composer-surface           │
│    --ds-menu-bg                         │
├─────────────────────────────────────────┤
│ L2 结构 token（表面/形状/阴影/动效）      │
│    --ds-surface-100 ... 400             │
│    --ds-radius-sm ... xl                │
│    --ds-elevation-100 ... 300           │
│    --ds-motion-swift ... bounce         │
├─────────────────────────────────────────┤
│ L1 语义色（品牌/文本/状态，随主题变）      │
│    --ds-brand-primary                   │
│    --ds-text-primary/secondary/tertiary │
│    --ds-danger/success/warning          │
└─────────────────────────────────────────┘
```

**硬规则**
- L1 只放"会随主题变"的色值
- L2 只放"结构"，同主题下恒定
- L3 只引用 L1/L2，**禁止出现裸 hex**
- 组件层禁止直接写 hex / rgb

---

## 3. 色彩规范

### 3.1 品牌色与强调色

| 语义 | 亮色 | 暗色 | 用途 |
|---|---|---|---|
| `--ds-brand-primary` | `#2d4739` | `#36d385` | 主品牌色，主按钮底 |
| `--ds-brand-primary-text` | `rgba(255,255,255,.9)` | `rgba(0,0,0,.9)` | 品牌色上的文字 |
| `--ds-accent` | `#8c7851` | `#36d385` | 强调/装饰（进度、加载点）|
| `--ds-glow-rgb` | `45,71,57` | `58,146,102` | 品牌色 RGB 分量，供发光效果 |

**为什么要单独存 `--ds-glow-rgb`**
CSS 无法对 hex 做 alpha 运算。存一份裸 RGB 分量，就能：
```css
box-shadow: 0 0 24px rgba(var(--ds-glow-rgb), 0.3);
```

### 3.2 文本三级（用透明度，不用灰阶）

| 语义 | 亮色 | 暗色 | 用途 |
|---|---|---|---|
| `--ds-text-primary` | `rgba(24,27,25,.9)` → 或 `#181b19` | `rgba(255,255,255,.9)` | 正文、标题 |
| `--ds-text-secondary` | `rgba(24,27,25,.64)` | `rgba(255,255,255,.5)` | 辅助说明 |
| `--ds-text-tertiary` | `rgba(24,27,25,.48)` | `rgba(255,255,255,.38)` | 占位、禁用、元信息 |
| `--ds-icon` | `#7e7f7e` | `#7e7f7e` | 图标（独立于文字）|

**规范**：用 `rgba` 而非固定灰。文字自动继承背景明暗，在任意 surface 上叠加都自然。

**透明度阶梯**：`.9 / .64 / .48`（亮色）、`.9 / .5 / .38`（暗色）。
暗色透明度更低，因为亮底叠黑需要更高不透明度才有对比。

### 3.3 状态色

| 语义 | 亮色 | 暗色 | 文字底 |
|---|---|---|---|
| `--ds-danger` | `#b64a34` | `#e56555` | `color-mix(in srgb, var(--ds-danger) 8%, var(--ds-surface-100))` |
| `--ds-success` | `#287d46` | `#4ade80` | 同上，`12%`（暗色）|
| `--ds-warning` | `#916300` | `#e0a830` | 同上 |

**关键规范：状态底色用 `color-mix` 派生，禁止硬编码。**
```css
--ds-danger-bg: color-mix(in srgb, var(--ds-danger) 8%, var(--ds-surface-100));
```
换主题时自动适配。亮色用 `8%`，暗色用 `12%`（深底需要更高混合比例才可见）。

### 3.4 交互态底色

| 语义 | 亮色 | 暗色 |
|---|---|---|
| `--ds-on-surface` | `rgba(55,61,58,.06)` | `rgba(168,184,176,.08)` |
| `--ds-on-surface-active` | `rgba(55,61,58,.08)` | `rgba(168,184,176,.16)` |

用途：hover 底、选中底、次级按钮底。**暗色的 active 是 hover 的两倍**，因为深底上差异更难感知。

### 3.5 分隔线

```css
--ds-divider: color-mix(in srgb, var(--ds-text-primary) 6%, transparent);
/* 暗色 12% */
```
用文字色派生，而非固定灰。6% 亮色 / 12% 暗色。

---

## 4. 表面与层级

### 4.1 四级表面

| Token | 亮色 | 暗色 | 语义 |
|---|---|---|---|
| `--ds-surface-100` | `#ffffff` | `#111212` | 最亮层：卡片、弹窗内容 |
| `--ds-surface-200` | `#faf9f5` | `#171818` | 应用底：主内容区 |
| `--ds-surface-300` | `#f6f4ef` | `#232524` | 次级面板：侧栏、内嵌区 |
| `--ds-surface-400` | `#f2eee6` | `#272a28` | 最深层：凹陷、代码块底 |

**规范**：必须 4 级。只用 bg/card 两级会导致"面板嵌在卡片里"时无处表达，
只能靠临时蒙半透明，最终层级混乱。

**暗色的方向反过来**：亮色 100 最亮（白），暗色 100 最暗（近黑），
即"100 永远是内容层，400 永远是背景层"。**按语义编号，不按明度编号。**

### 4.2 浮层表面

```css
--ds-popover:     var(--ds-surface-100);   /* 暗色 surface-300 */
--ds-menu-bg:     color-mix(in oklch, var(--ds-popover) 96%, white);
--ds-overlay:     rgba(0,0,0,.3);          /* 暗色 .6 */
--ds-browser-canvas: #ffffff;              /* 暗色 #1c1c1c */
```

**规范**：浮层比其下的表面**高一档**（亮色）或**低一档**（暗色）。

### 4.3 z-index 层级表

实测层级分布，整理为规范：

| 层 | z-index | 内容 |
|---|---|---|
| 底 | `-1` | 壁纸、背景装饰 |
| `1~5` | 内容层 | 卡片、列表项 |
| `10` | 吸顶 | sticky header |
| `20` | 侧栏 | 折叠/展开的侧栏 |
| `30` | 常驻浮层 | webview 层、内嵌浏览器 |
| `40` | 弹层 | Popover / Menu / Tooltip |
| `50` | 模态 | Dialog / Drawer |
| `9999+` | 系统级 | Toast、崩溃提示、启动 splash |

**硬规则：禁止使用任意数字。** z-index 必须来自这张表。
需要"压过某层但低于某层"时，在两层之间预留空隙（如 31 = webview 之上、Popover 之下）。

---

## 5. 形状规范

### 5.1 圆角阶梯（可全局缩放）

```css
--ds-corner-radius-scale: 1;    /* 全局开关：0.25 = 方正，2.5 = 圆润 */

--ds-radius-sm:  calc(8px  * var(--ds-corner-radius-scale, 1));
--ds-radius-md:  calc(12px * var(--ds-corner-radius-scale, 1));
--ds-radius-lg:  calc(18px * var(--ds-corner-radius-scale, 1));
--ds-radius-xl:  calc(24px * var(--ds-corner-radius-scale, 1));
--ds-radius-pill: 9999px;       /* 胶囊不参与缩放 */
```

| 阶梯 | 基准 | 典型用途 |
|---|---|---|
| `sm` | 8px | 小标签、徽章、行内元素 |
| `md` | 12px | 列表项、输入框、小卡片 |
| `lg` | 18px | 卡片、面板 |
| `xl` | 24px | 大卡片、容器 |
| `pill` | 9999px | 按钮、标签、头像框 |

**规范**：圆角用乘法而非四套固定值。一个变量控制整体视觉性格。

### 5.2 超椭圆（关键差异点）

```css
.corner-squircle {
  border-radius: 16px;
  corner-shape: superellipse(1.5);
}
.corner-round {
  border-radius: 9999px;
  corner-shape: round;
}
```

| 写法 | 视觉 |
|---|---|
| 仅 `border-radius` | 几何圆弧，曲率在拐点突变，偏硬 |
| `+ corner-shape: superellipse(1.5)` | 曲率连续过渡，柔和但不失结构 |

**规范**
- 矩形控件（卡片、按钮、输入框）→ `superellipse(1.5)`
- 本该是圆的（头像、状态点、圆点）→ `round`
- **不要混用**，否则视觉不统一

**指数选择**：`1.5` 是"柔和有结构"的常用值；`2` 更接近标准 squircle（更方）；越大越方。

**兼容**：Chromium 139+ 支持。不支持时自动退化为普通圆角，需在视觉验收中确认可接受。

### 5.3 圆角继承

```css
[data-ds-round-control] > [data-ds-corner-inherit] {
  corner-shape: inherit;
}
```
按容器尺寸动态算圆角时容易断裂，用继承修正。

---

## 6. 阴影规范

### 6.1 三级 elevation

**elevation-100 —— 静止卡片微浮**
```css
inset 0.5px 0.5px 0.5px #FFFFFF,              /* 顶部内高光 */
0 0 0.5px 0.5px rgba(0,0,0,0.04),             /* 极细描边（代替 border）*/
0.5px 0.5px 1px rgba(0,0,0,0.08)              /* 右下投影 */
```

**elevation-200 —— 悬浮卡片**
```css
0 0 0 0.5px rgba(45,71,57,0.12),
0 9px 4px rgba(0,0,0,0.01),
0 5px 3px rgba(0,0,0,0.02),
0 2px 2px rgba(0,0,0,0.03),
0 1px 1px rgba(0,0,0,0.04),
inset 0.5px 0.5px 0.5px rgba(255,255,255,0.85)
```

**elevation-300 —— 弹层/模态（7 层软阴影）**
```css
inset 0 0 0.5px 0.5px rgba(255,255,255,0.8),
0 0 0 0.5px rgba(0,0,0,0.04),
0 3px 6px rgba(0,0,0,0.04),
0 11px 11px rgba(0,0,0,0.03),
0 24px 15px rgba(0,0,0,0.02),
0 43px 17px rgba(0,0,0,0.01),
0 68px 19px rgba(0,0,0,0)
```

**构造规律（可自行推演任意层级）**
1. 层数 3~7 层，越大越高越多层
2. y 偏移递增：`1 → 3 → 11 → 24 → 43 → 68`
3. 模糊半径 ≈ 0.6×y 偏移
4. **alpha 严格递减**：`.08 → .04 → .03 → .02 → .01 → 0`
5. **内层 + 一个 inset 高光**（模拟从上方来的光）

**暗色模式全部改写**（不能直接复用）：
```css
--ds-elevation-100: 0 0 0 0.5px rgba(255,255,255,0.08);
--ds-elevation-200: inset 0 0 0 0.5px rgba(255,255,255,0.12),
                    0 0 9px -4px rgba(0,0,0,0.40),
                    0 0 32px -8px rgba(0,0,0,0.16);
--ds-elevation-300: inset 0 0 0 0.5px rgba(255,255,255,0.12),
                    0 2px 12px -6px rgba(0,0,0,0.44),
                    0 5px 36px -12px rgba(0,0,0,0.20);
```
暗色变成**负扩散（spread 为负）+ 更低层数**，因为深底上大范围柔光会糊成一片。

### 6.2 用阴影做边框

```css
/* 推荐 */
box-shadow: 0 0 0 0.5px rgba(0,0,0,0.04);
/* 不推荐 */
border: 1px solid #eee;
```

优势：不占布局空间、不触发 reflow、可叠入阴影链、支持 0.5px 亚像素。

### 6.3 clip-gutter（裁剪阴影问题）

```css
--ds-elevation-100-clip-gutter-block: 2px;   /* 亮色 1px 暗色 */
--ds-chat-composer-shadow-clip-gutter: 32px;
```

容器 `overflow: hidden` 会切掉外阴影。规范：
- 在容器内预留固定 gutter 给阴影
- 或用 `drop-shadow` 滤镜版（`--ds-elevation-100-filter`）走滤镜链

### 6.4 输入框阴影四态

```css
--ds-input-shadow:        0 0 0.5px 0.5px rgba(0,0,0,0.1), 0.5px 0.5px 1px rgba(0,0,0,0.18);
--ds-input-shadow-inset:  inset 0.5px 0.5px 0.5px 0 white;
--ds-input-shadow-hover:  ... 0 0 0 0.5px color-mix(in srgb, var(--ds-brand-primary) 24%, transparent) ...
--ds-input-shadow-focus:  ... 0 0 0 3px color-mix(in srgb, var(--ds-brand-primary) 10%, transparent) ...
```

**规范**：焦点环 = 1px 品牌色 24% + 3px 品牌色 10% 光晕，双层叠加。
不要单层粗描边（显得生硬）。

---

## 7. 字体与排版

### 7.1 字体栈

```css
--ds-font-sans:  'Inter Variable', Inter, 'Noto Sans SC Variable',
                 ui-sans-serif, system-ui, -apple-system, 'PingFang SC',
                 'Microsoft YaHei', sans-serif;
--ds-font-serif: 'Noto Serif SC Variable', 'Noto Serif SC', Georgia,
                 'Songti SC', serif;
--ds-font-mono:  ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
                 'Liberation Mono', 'Courier New', monospace;
--ds-font-kbd:   'Inter Variable', 'Inter', sans-serif;
--ds-font-display-number: 'Spline Sans Mono', ui-monospace, monospace;
```

**规范**
- 西文优先（Inter），中文回退（Noto Sans SC / PingFang / 雅黑）
- 等宽用系统栈，不引字体
- **数字用等宽**（`font-display-number`）—— 计数器、时长、统计值需要跳变时不抖动

### 7.2 字号阶梯（实测高频值）

| Token | 值 | 用途 | 出现频次 |
|---|---|---|---|
| `text-2xs` | **10px** | 极小标签、角标 | 低 |
| `text-xs` | **11px** | 元信息、提示、时间 | 13 |
| `text-xs+` | **12px** | 次要正文、小按钮 | 14 |
| `text-sm` | **13px** | **基准正文** ★ | **30（最高）** |
| `text-base` | **14px** | 强调正文 | 5 |
| `text-md` | **15px** | 大正文 | 1 |
| `text-lg` | **16px** | 小标题 | 1 |
| `text-xl` | **20px** | 标题 | 2 |
| `text-2xl` | **22~24px** | 大标题 | 3 |
| `text-3xl` | **28px** | 页面标题 | 2 |
| `text-4xl` | **36px** | 展示型 | 1 |
| `text-5xl` | **40px** | Hero | 1 |

**关键规范：基准正文是 13px，不是 16px。**
桌面应用信息密度高，13px 是"能长时间阅读 + 信息密度足够"的平衡点。
Web 常规的 16px 在桌面端会显得过大。

### 7.3 行高

| Token | 值 | 用途 |
|---|---|---|
| `leading-none` | `1` | 单行标题、按钮文字 |
| `leading-tight` | `1.25` | 紧凑标题 |
| `leading-normal` | `1.5` | 一般正文 |
| `leading-relaxed` | `1.625` | **长文正文** ★ |
| `leading-loose` | `1.75` | 宽松阅读 |

**规范**
- 字号 ≤ 13px → 行高 `1.5`
- 正文阅读 → `1.625`
- 标题 → `1.25`
- 按钮/标签 → `1`

### 7.4 字重

| 值 | 用途 |
|---|---|
| 400 | 正文 |
| 500 | **UI 控件默认**（按钮、标签、菜单项）|
| 600 | 强调、小标题 |
| 700 | 大标题（少用）|

**规范：UI 控件默认 500，不是 400。** 小字号（13px）在 400 下会显得绵软，
500 让控件"立"起来。正文段落仍用 400。

### 7.5 字体特性

```css
font-variant-numeric: tabular-nums;   /* 数字等宽，计数器防抖 */
font-feature-settings: 'ss01', 'cv11'; /* Inter 的替代字形 */
letter-spacing: -0.011em;              /* 大字号收紧 */
```

**规范**：所有会动态变化的数字（计时、计数、进度百分比）必须 `tabular-nums`，
否则数字宽度变化会导致整行抖动。

---

## 8. 间距与栅格

### 8.1 基础步长 4px

实测间距值分布：
```
8px  (35次) ★ 最常用
4px  (25次)
2px  (18次)
10px (17次)
6px  (15次)
12px (11次)
16px (8次)
```

**规范：4px 基准栅格，允许 2px 微调。**

| Token | 值 | 典型用途 |
|---|---|---|
| `space-0.5` | 2px | 图标与文字间、行内微调 |
| `space-1` | 4px | 紧凑内距、标签内 padding |
| `space-1.5` | 6px | 列表项垂直内距 |
| `space-2` | **8px** ★ | 组件内距、卡片 padding |
| `space-2.5` | 10px | 列表项水平内距 |
| `space-3` | 12px | 区块间、弹窗内距 |
| `space-4` | 16px | 面板 padding |
| `space-6` | 24px | 大区块分隔 |
| `space-8` | 32px | 页面级留白 |

### 8.2 组件内距规范

| 组件 | 内距 |
|---|---|
| 列表项 | `8px 10px` |
| 小按钮 | `4px 8px` |
| 标准按钮 | `6px 12px` |
| 卡片 | `8px`（用 `gap-px` 做分隔时）|
| 弹窗 | `12px ~ 16px` |
| 面板 | `16px` |

### 8.3 列表分隔用 gap 不用 border

```css
.ds-list {
  display: flex; flex-direction: column;
  gap: 1px;                          /* 露出容器底色当分隔线 */
  background: var(--ds-surface-200); /* 分隔线颜色 = 底 */
}
.ds-list-item { background: var(--ds-surface-100); border-radius: var(--ds-radius-md); }
```

**优势**：hover 高亮时不会出现"边框线被高亮块压住"的断层。

---

## 9. 动效规范

### 9.1 时长阶梯

```css
--ds-duration-fast:   180ms;   /* 微交互：hover、focus、颜色 */
--ds-duration-base:   240ms;   /* 常规：展开、淡入、状态切换 */
--ds-duration-slow:   350ms;   /* 较大位移、抽屉 */
--ds-duration-layout: 500ms;   /* 布局重排、面板尺寸变化 */
--ds-duration-bounce: 700ms;   /* 回弹类（必须配过冲曲线）*/
```

**规范**
- 180ms 以下不感知，350ms 以上开始觉得慢
- **超过 400ms 只允许用在"一次性强调"**（如 Toast 入场）
- 高频重复操作（打字、滚动）→ 一律 ≤ 180ms 或不做动画

### 9.2 缓动曲线

```css
--ds-ease-swift:         cubic-bezier(0.33, 1, 0.68, 1);      /* 快出慢停，通用 */
--ds-ease-soft:          cubic-bezier(0.22, 1, 0.36, 1);      /* 柔和，布局 */
--ds-ease-spring:        cubic-bezier(0.34, 1.3, 0.64, 1);    /* 轻微过冲 */
--ds-ease-spring-gentle: cubic-bezier(0.34, 1.15, 0.64, 1);   /* 更轻过冲 */
--ds-ease-bounce:        cubic-bezier(0.68, -0.55, 0.265, 1.55); /* 明显回弹 */
```

**解读**
- 第二个控制点 y > 1（如 `1.3`）→ **过冲**（冲过目标再回）
- 第一个控制点 y < 0（如 `-0.55`）→ **反向预备**（先往回再冲）
- `bounce` 必须配 700ms，否则显得廉价

**选择规范**
| 场景 | 曲线 |
|---|---|
| hover / focus / 颜色 | `swift` + 180ms |
| 展开 / 淡入 / 状态 | `soft` + 240ms |
| 元素出现（要"活"）| `spring` + 240ms |
| 布局重排 | `soft` + 500ms |
| 强调反馈（开关、Toast）| `bounce` + 700ms |

### 9.3 组合 token（降低误用的关键）

```css
--ds-motion-swift:  var(--ds-duration-fast)   var(--ds-ease-swift);
--ds-motion-soft:   var(--ds-duration-base)   var(--ds-ease-soft);
--ds-motion-spring: var(--ds-duration-base)   var(--ds-ease-spring);
--ds-motion-layout: var(--ds-duration-layout) var(--ds-ease-soft);
--ds-motion-bounce: var(--ds-duration-bounce) var(--ds-ease-bounce);
```

**规范：必须同时提供原子 token 和组合 token。**
开发者写 `transition: all var(--ds-motion-soft)` 一行就对，不用每次纠结配哪个 ease。

### 9.4 动效设计模式

**模式 A：轨道 + 相位（加载态）**
两个点沿轨道运动，一个缩放放大时另一个缩小，相位差 180°。
```js
const step = size + gap;  const box = size + step;   // 容器 = 点 + 步长
// 点A: scale 1 ↔ 0.4, opacity 1 ↔ 0.35
// 点B: 相位反转
```
优于 spinner：视觉密度低，长时间显示不烦躁。

**模式 B：状态呼吸（AI/异步任务）**
不同内在状态用不同呼吸节奏，而非同一个转圈：
```css
@keyframes pulse-idle      { /* 慢，2.4s */ }
@keyframes pulse-thinking  { /* 中，1.6s，亮度波动 */ }
@keyframes pulse-producing { /* 快，1.0s，位移+缩放 */ }
@keyframes pulse-tool      { /* 交替 A/B，表示工具调用 */ }
```

**模式 C：分层入场（复杂组件）**
不要整体淡入，而是按信息层级错峰：
```css
.item:nth-child(odd)  { animation: reveal-odd  240ms var(--ds-ease-soft) both; }
.item:nth-child(even) { animation: reveal-even 240ms var(--ds-ease-soft) 40ms both; }
```

**模式 D：微交互拆分**
一个图标的 hover 可拆成多个独立部件动画（位移、缩放、描边、光晕分离），
而非整体 scale(1.1)。

---

## 10. 组件规范

### 10.1 按钮

**变体（按语义，不按视觉）**

| variant | 视觉 | 使用场景 | 每屏上限 |
|---|---|---|---|
| `primary` | 品牌色实心 | 主行动 | **1 个** |
| `secondary` | 透明 + 1px 内描边 | 次要强调 | 2~3 个 |
| `tertiary` | `--ds-on-surface` 半透明底 | 工具类操作 | 不限 |
| `ghost` | 纯文字，hover 才显底 | 低干扰、图标按钮 | 不限 |
| `danger` | 危险色 6% 底 + 危险色文字 | 破坏性操作 | 视场景 |

**尺寸**

| size | 高度 | 水平内距 | 字号 | 图标 |
|---|---|---|---|---|
| `mini` | 24px | 8px | 11px | 14px |
| `small` | 28px | 10px | 12px | 15px |
| `default` | 32px | 12px | 13px | 16px |
| `large` | 36px | 20px | 14px | 18px |

**规范：图标比同档字号大 1~2px。** 图标有内边距，视觉上会显得小，需要补偿。

**实现要点**
```css
/* 边框用 outline，不占布局 */
outline: 1px solid var(--ds-brand-primary);
outline-offset: -1px;         /* 画在内侧 */

/* 圆角用 pill */
border-radius: var(--ds-radius-pill);

/* 图标尺寸用 CSS 变量传给子元素 */
--ds-btn-icon-size: 16px;
```

**交互态**
```css
hover  : opacity 0.8（实心变体）| 半透明底（ghost/tertiary）
active : opacity 0.7
focus  : 焦点环（见 6.4）
disabled: opacity 0.5 + cursor: default
```

**注意：实心变体 hover 用 opacity 而非换色** —— 换色会让品牌色失准，opacity 保持色相。

### 10.2 列表

```css
.ds-list { display: flex; flex-direction: column; gap: 1px; padding: 6px; }
.ds-list-item {
  padding: 8px 10px;
  border-radius: var(--ds-radius-md);
  font-size: 13px;
}
.ds-list-item:hover  { background: var(--ds-on-surface); }
.ds-list-item[active]{ background: var(--ds-on-surface); }
.ds-list-item[disabled] { opacity: 0.5; cursor: not-allowed; }
```

**规范**
- 容器 padding 6px，项间距 1px（gap 露底当分隔线）
- 项内距 `8px 10px`
- 项圆角 `md`（12px）
- hover 与 active 用同一个底色 token

### 10.3 输入框

```css
.ds-input {
  border-radius: var(--ds-radius-md);
  font-size: 13px;
  background: var(--ds-surface-input);
  box-shadow: var(--ds-input-shadow);
}
.ds-input:hover { box-shadow: var(--ds-input-shadow-hover); }
.ds-input:focus { box-shadow: var(--ds-input-shadow-focus); }
```

**规范**：输入框底与容器底要**有区分**（`--ds-surface-input` 指向 surface-100 或 300）。

### 10.4 数据属性契约（样式集中管理）

不在 className 里散落视觉值，改用 `data-*` 属性：

```css
[data-ds-pill-control]  { corner-shape: round; }
[data-ds-round-control] { corner-shape: round; }
[data-ds-inset-outline] { /* 常驻内描边，不是焦点框 */ }
[data-ds-scroll-glass]  { /* 玻璃拟态滚动区 */ }
```

组件里只写 `data-ds-pill-control="true"`，样式规则集中一处。

**规范**：可复用的视觉特征 → `data-*` 契约；一次性布局 → className。

---

## 11. 状态与反馈

### 11.1 反馈层级（按侵入度递增）

| 层级 | 组件 | 何时用 | 停留 |
|---|---|---|---|
| 0 | 无反馈（仅 hover 变色）| 纯展示 | — |
| 1 | 内联指示（图标变、文字变）| 局部小动作 | 常驻 |
| 2 | Tooltip | 补充说明 | hover 期间 |
| 3 | Toast | 操作结果、非阻塞 | **3~5s 自动消失** |
| 4 | Banner | 需要用户知晓但可忽略 | 手动关闭 |
| 5 | Dialog | 需要确认或输入 | 阻塞至响应 |
| 6 | 全屏阻塞（Splash）| 首次启动、致命错误 | 阻塞至完成 |

**规范：能用低层级解决就不要升级。**
"保存成功"用 Toast（3），不要弹 Dialog（5）。

### 11.2 Toast 规范

```css
[data-toast-viewport] {
  /* 用 CSS Anchor 对齐到主内容卡片，不用 JS 算坐标 */
  top:   calc(anchor(--app-toast-card top, 8px) + 8px);
  right: calc(anchor(--app-toast-card right, 8px) + 8px);
}
```
```css
[data-workspace-content-frame] { anchor-name: --app-toast-card; }
```

**规范**
- 对齐到**主内容区域**右上角，避开窗口控制按钮
- 用 CSS Anchor Positioning，窗口缩放/侧栏折叠自动跟随
- 成功类 3s，错误类 5s 或手动关闭
- 危险操作 Toast 必须带 action 按钮（撤销/查看详情）

### 11.3 加载态选择

| 场景 | 方案 |
|---|---|
| < 300ms | **不显示任何 loading**（闪烁更糟）|
| 300ms ~ 2s | 骨架屏（Skeleton）|
| > 2s，有进度 | 进度条 |
| > 2s，无进度 | 轨道动画 / 状态呼吸 |
| 首次启动 | 全屏 Splash（与正式界面同结构）|

**规范：300ms 以下的加载必须不显示指示器。** 加了反而显得卡。

### 11.4 空状态

必须包含三要素：
1. **图示**（轻量图标或插画）
2. **一句话说明为什么是空的**
3. **一个明确的下一步行动**

```jsx
<DsEmpty
  icon={<Icon name="inbox" />}
  title="还没有开始对话"
  description="把工作区路径设为项目目录，AI 才能读写文件"
  action={<DsButton onClick={pickFolder}>选择目录</DsButton>}
/>
```

---

## 12. 无障碍规范

### 12.1 对比度（强制）

- 正文 / 图标：**≥ 4.5:1**
- 大字（≥ 18px 或 14px 粗体）：**≥ 3:1**
- 非文本（边框、图形）：**≥ 3:1**

**用户输入的颜色必须校验**

```js
function readableOn(foreground, background) {
  const hsl = rgbToHsl(foreground);
  // 1. 提亮度直到达标
  for (let l = Math.max(hsl[2], 0.62);
       l <= 0.84 && contrast(candidate, background) < 4.5;
       l += 0.04) {
    candidate = hslToHex(hsl[0], Math.max(hsl[1], 0.48), l);
  }
  // 2. 兜底：向白色混入
  for (let i = 0; i <= 10 && contrast(candidate, background) < 4.5; i++) {
    candidate = mix(candidate, '#ffffff', (i + 1) / 10);
  }
  return candidate;
}
```

**规范：不要信任用户输入的颜色。** 自动调整到达标，而不是照单全收。

### 12.2 焦点管理（鼠标/键盘分离）

```css
/* 鼠标操作：不显示焦点框 */
:root[data-pointer-focus] :focus:not([data-ds-inset-outline="true"]) {
  outline: none !important;
  outline-color: transparent !important;
}
```
```js
onPointerDown → root.setAttribute('data-pointer-focus', '')
onKeyDown（非修饰键）→ root.removeAttribute('data-pointer-focus')
```

**规范**
- 不要只用 `:focus-visible` 一把梭
- **排除常驻内描边**（`[data-ds-inset-outline]`）—— 它不是焦点框，不能被清除
- `outline-color: transparent` 必须显式写：shorthand 会把颜色重置为 `currentColor`

### 12.3 动效降级（双重保险）

```css
/* 系统级 */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; }
}
/* 应用级（用户在设置里手动关）*/
.reduce-motion * { animation: none !important; }
```

**规范：两个都要做。** 系统级是 OS 设置，应用级是产品内开关。

### 12.4 语义与键盘

- 可点击的非 `<button>` 元素必须：`role="button"` + `tabIndex={0}` + Enter/Space 处理
- 禁用态：`aria-disabled="true"` + `cursor: not-allowed`（**不要用 `pointer-events: none`**，会失去焦点能力）
- 展开态：`aria-expanded`
- 图标按钮必须有 `aria-label`
- 装饰性动画容器标 `aria-hidden="true"`

---

## 13. 主题化规范

### 13.1 主题只覆盖 L1 语义色

```css
[data-theme="ocean"] {
  --ds-brand-primary: #0b6a9e;
  --ds-surface-200:   #f0f9ff;
  --ds-text-primary:  #0c4a6e;
  /* 形状、动效、间距全部继承 */
}
```

**规范：形状 / 动效 / 间距 token 绝对不进主题覆盖。**
否则换主题会破坏布局节奏。实测 12 套主题，每套只覆盖 20~30 个色 token。

### 13.2 暗色覆盖清单（82 项）

必须覆盖的类别（缺一个就会出现亮色残留）：
1. 文本三级 + 图标
2. 品牌色 + accent + glow-rgb
3. 状态色 3 个 + 状态底 3 个
4. 表面 4 档 + 输入底
5. 交互态 2 个（on-surface / active）
6. 分隔线 + overlay
7. 浮层 3 个（popover / menu-bg / browser-canvas）
8. 阴影 3 档 + 输入阴影 4 态
9. clip-gutter 2 个
10. workspace-tab 类特例

### 13.4 ⚠️ 换主题失效的头号陷阱（实测踩出）

**现象**：切到暗色后，卡片、body 变了，但**按钮背景还是亮色的品牌色**。

**复现（已在这一套 token 上实际跑出）**：
```css
.btn-primary {
  background: var(--ds-brand-primary);
  transition: opacity 180ms, background 240ms;  /* ← 元凶在这 */
}
```
切换到 `.dark` 后实测：
```
--ds-brand-primary 计算值   #2d4739 → #36d385   ✅ token 已正确切换
按钮 background 计算值      rgb(45,71,57)        ❌ 没跟着变
```

**根因**：
当 `background` 同时满足三个条件时，浏览器会**把过渡起点值锁定**，不再重算：
1. 属性值来自 `var()` 间接引用
2. 该 `var()` 的值因祖先类切换而改变
3. 属性本身在 `transition` 列表里

`color` 有时幸免，`background` / `box-shadow` 必中。这是 Chromium 的实现细节，不是规范写法问题。

**正确做法**

```css
/* ✅ 只过渡"不会因主题而变"的属性 */
.btn {
  transition: opacity 180ms var(--ds-ease-swift),
              transform 180ms var(--ds-ease-swift);
}

/* ✅ 如果确实要过渡 background（如 hover 变色），
      就用"同一 token 的不同透明度"，而不是跨 token 切换 */
.btn-ghost { background: transparent; }
.btn-ghost:hover { background: var(--ds-ghost-hover-bg); }  /* 同一个 token 内的透明度变化，安全 */

/* ❌ 危险：主题相关属性进 transition */
.btn { transition: background 240ms, color 240ms; }
```

**额外两个必查项**

**① 不要用嵌套 `var()` 的 fallback 形式**
```css
/* ❌ 这种写法在切换主题时容易不重算 */
background: var(--ds-action-primary, var(--ds-brand-primary));

/* ✅ 直接用最终变量 */
background: var(--ds-brand-primary);
```
实测：嵌套 fallback 下 `background` 完全不跟随 `.dark`，而同一行的 `color` 却变了。

**② token 引用链不要超过 2 层**
```css
/* ✅ 2 层，安全 */
--ds-action-primary: var(--ds-brand-primary);

/* ⚠️ 3 层以上容易在不同浏览器出现解析差异 */
--ds-a: var(--ds-b);
--ds-b: var(--ds-c);
--ds-c: #2d4739;
```

**排查清单（换主题后部分元素不变色时按序检查）**
1. 该元素的 `transition` 里有没有 `background` / `box-shadow` / `color`？→ 移除
2. 该属性是不是 `var(--a, var(--b))` 嵌套 fallback？→ 展平
3. 该 token 是否在 `.dark` 块里漏了？→ 比对覆盖清单
4. 是否有内联 `style` 写死了值？→ 改为 token
5. 是否有 `!important` 在更早的规则里锁定了？→ 检查优先级
6. 是否 `@media` 里的亮色规则在暗色下仍命中？→ 补暗色分支

在框架挂载**之前**，用同步内联脚本算好主题并写入 CSS 变量：

```html
<script>
(function () {
  // 1. 读持久化设置（同步，无 await）
  var state = JSON.parse(localStorage.getItem('app-settings') || '{}').state;
  // 2. 选调色板
  var palette = palettes[state.colorTheme]?.[isDark ? 'dark' : 'light'];
  // 3. 对比度校验（用户自定义色）
  if (state.customAccent) palette.primary = readableOn(state.customAccent, palette.bg);
  // 4. 写入 boot 变量
  root.style.setProperty('--boot-theme-bg', palette.bg);
  root.style.setProperty('--boot-theme-primary', palette.primary);
})();
</script>
```

**规范**
- 必须**同步执行**，不能 `async` / `await` / 放 `DOMContentLoaded`
- Splash 的结构与间距要和正式界面完全一致（logo → slogan → 加载动画）
- 主题就绪后再加 `boot-theme-ready` 类切换变量源

### 13.5 主题切换的实现方式

`.dark` 类加在 `<html>` 上（不是 `<body>`），配合 `color-scheme`：
```css
html { color-scheme: light; }
html.dark { color-scheme: dark; }
```
好处：原生控件（滚动条、日期选择器、`input[type=color]`）自动跟随，不用手写。

切换时**不要**做 `background` 过渡（见 §13.4），要过渡就用伪元素单独做覆盖层淡入。

---

## 14. 工程落地清单

### 14.1 现代 CSS 特性（可放心用）

| 特性 | 用途 | 兼容 |
|---|---|---|
| `color-mix()` | 主题派生色 | Chrome 111+ |
| `oklch()` | 更均匀的混色 | Chrome 111+ |
| `corner-shape` | squircle | Chrome 139+（需降级）|
| `anchor-name` / `anchor()` | 浮层定位 | Chrome 125+ |
| `view-transition` | 共享元素动画 | Chrome 111+ |
| `@container` | 组件级响应式 | Chrome 105+ |
| `:has()` | 父选择 | Chrome 105+ |
| `text-wrap: balance/pretty` | 标题折行 | Chrome 114+ |
| `backdrop-filter` | 玻璃拟态 | 全支持 |

### 14.2 必须做的检查

- [ ] 所有颜色来自 token，组件层无裸 hex
- [ ] 暗色模式覆盖 82 项，无亮色残留
- [ ] 圆角全部走 `calc(base * scale)`
- [ ] 矩形控件用 `superellipse(1.5)`，圆形用 `round`
- [ ] 边框用 `0 0 0 0.5px` 阴影，不用 `border`
- [ ] 阴影暗色模式单独重写（不能复用亮色）
- [ ] 基准正文 13px，UI 控件字重 500
- [ ] 动态数字用 `tabular-nums`
- [ ] 间距走 4px 栅格
- [ ] z-index 只用层级表里的值
- [ ] 动效用组合 token
- [ ] `< 300ms` 加载不显示指示器
- [ ] 用户自定义色过 WCAG 4.5:1
- [ ] `prefers-reduced-motion` + 应用级开关双保险
- [ ] 首屏主题同步预计算，零闪烁

### 14.3 Electron / 桌面端特例

```js
// webview / iframe 会吞 mouseleave → 按钮卡在 hover 态
// 解决：全局 pointerover 捕获
window.addEventListener('pointerover', clearIfOutside, { capture: true, passive: true });
window.addEventListener('blur', clear);
```

```css
/* 透明窗口必须显式清背景，否则根容器底色会把它涂不透明 */
html.boot-transparent, html.boot-transparent body, html.boot-transparent #root {
  background: transparent !important;
}
```

```js
// 窗口拖动：非 macOS 需自实现 drag 区域，且要处理双击最大化
```

### 14.4 性能

- 大列表用虚拟滚动（`@tanstack/react-virtual`）
- 图标按需引入，不要整包导入
- 图表/编辑器/富文本 → 独立 chunk 懒加载
- `will-change` 只加在动画期间，结束后移除
- 阴影层数多时考虑 `filter: drop-shadow` 走 GPU

---

## 15. 反模式清单

| ❌ 反模式 | ✅ 正确做法 |
|---|---|
| 只有 bg / card 两级表面 | 4 级 surface-100~400 |
| `dark:` 前缀逐个写 class | token 覆盖，组件零改动 |
| 暗色 = 亮色变暗 | 暗色单独选色相 |
| 文字用固定灰 `#666` | `rgba` 三级，自动适配背景 |
| 状态底写死 `#fef2f2` | `color-mix(in srgb, danger 8%, surface-100)` |
| `border: 1px solid #eee` | `box-shadow: 0 0 0 0.5px rgba(0,0,0,.04)` |
| `box-shadow: 0 4px 12px rgba(0,0,0,.1)` | 5~7 层递减软阴影 |
| 暗色复用亮色阴影 | 暗色用负 spread + 更少层数 |
| 固定 `border-radius: 12px` | `calc(12px * scale)` |
| 全部 `border-radius` | 矩形用 `superellipse(1.5)` |
| 基准字号 16px | 桌面端 13px |
| UI 控件 400 字重 | 500 |
| 数字不设 `tabular-nums` | 必须设，防抖动 |
| 间距 7px / 9px / 13px | 走 4px 栅格（允许 2px）|
| z-index 随手写 `999` | 用层级表 |
| 只给原子动效 token | 同时给组合 token |
| `transition: all 0.2s ease` | `var(--ds-motion-soft)` |
| `transition: background/color` + 主题 token | **不过渡主题相关属性**（会锁死旧值，见 §13.4）|
| `var(--a, var(--b))` 嵌套 fallback | 展平为单个最终变量 |
| token 引用链 3 层以上 | 最多 2 层 |
| 加载就转圈 | < 300ms 不显示；长任务用状态呼吸 |
| `:focus-visible` 一把梭 | `data-pointer-focus` 分离鼠标/键盘 |
| 信任用户输入的颜色 | 强制 WCAG 校验 |
| 只做 `prefers-reduced-motion` | 系统级 + 应用级双保险 |
| 首屏白屏再刷色 | 同步脚本预计算 |
| `pointer-events: none` 做禁用 | `aria-disabled` + `cursor` |
| 图标整包导入 | 按需 + 独立 chunk |
| hover 换色做反馈 | 实心变体用 opacity 保色相 |

---

## 附录 A：完整 Token 清单（可直接复制）

```css
:root {
  /* ===== L1 语义色 ===== */
  --ds-brand-primary: #2d4739;
  --ds-brand-primary-text: rgba(255,255,255,0.9);
  --ds-accent: #8c7851;
  --ds-accent-text: rgba(255,255,255,0.9);
  --ds-glow-rgb: 45,71,57;

  /* action 层：把"可操作色"与"品牌色"解耦，未来可单独调 */
  --ds-action-primary: var(--ds-brand-primary);
  --ds-action-primary-text: var(--ds-brand-primary-text);
  --ds-action-subtle-surface: var(--ds-on-surface);
  --ds-action-subtle-text: var(--ds-text-primary);

  --ds-text-primary: #181b19;
  --ds-text-secondary: rgba(24,27,25,0.64);
  --ds-text-tertiary: rgba(24,27,25,0.48);
  --ds-icon: #7e7f7e;

  --ds-danger: #b64a34;
  --ds-success: #287d46;
  --ds-warning: #916300;
  --ds-danger-bg: color-mix(in srgb, var(--ds-danger) 8%, var(--ds-surface-100));
  --ds-success-bg: color-mix(in srgb, var(--ds-success) 8%, var(--ds-surface-100));
  --ds-warning-bg: color-mix(in srgb, var(--ds-warning) 8%, var(--ds-surface-100));

  /* ===== L2 表面 ===== */
  --ds-surface-100: #ffffff;
  --ds-surface-200: #faf9f5;
  --ds-surface-300: #f6f4ef;
  --ds-surface-400: #f2eee6;
  --ds-surface-input: var(--ds-surface-100);
  --ds-on-surface: rgba(55,61,58,0.06);
  --ds-on-surface-active: rgba(55,61,58,0.08);
  --ds-divider: color-mix(in srgb, var(--ds-text-primary) 6%, transparent);
  --ds-overlay: rgba(0,0,0,0.3);
  --ds-popover: var(--ds-surface-100);
  --ds-menu-bg: color-mix(in oklch, var(--ds-popover) 96%, white);
  --ds-pill-bg: color-mix(in srgb, var(--ds-brand-primary) 12%, transparent);
  --ds-ghost-hover-bg: var(--ds-on-surface);

  /* ===== L2 形状 ===== */
  --ds-corner-radius-scale: 1;
  --ds-corner-shape: round;
  --ds-radius-sm: calc(8px * var(--ds-corner-radius-scale, 1));
  --ds-radius-md: calc(12px * var(--ds-corner-radius-scale, 1));
  --ds-radius-lg: calc(18px * var(--ds-corner-radius-scale, 1));
  --ds-radius-xl: calc(24px * var(--ds-corner-radius-scale, 1));
  --ds-radius-pill: 9999px;

  /* ===== L2 阴影 ===== */
  --ds-elevation-100: inset 0.5px 0.5px 0.5px #FFFFFF,
                      0 0 0.5px 0.5px rgba(0,0,0,0.04),
                      0.5px 0.5px 1px rgba(0,0,0,0.08);
  --ds-elevation-200: 0 0 0 0.5px rgba(45,71,57,0.12),
                      0 9px 4px rgba(0,0,0,0.01),
                      0 5px 3px rgba(0,0,0,0.02),
                      0 2px 2px rgba(0,0,0,0.03),
                      0 1px 1px rgba(0,0,0,0.04),
                      inset 0.5px 0.5px 0.5px rgba(255,255,255,0.85);
  --ds-elevation-300: inset 0 0 0.5px 0.5px rgba(255,255,255,0.8),
                      0 0 0 0.5px rgba(0,0,0,0.04),
                      0 3px 6px rgba(0,0,0,0.04),
                      0 11px 11px rgba(0,0,0,0.03),
                      0 24px 15px rgba(0,0,0,0.02),
                      0 43px 17px rgba(0,0,0,0.01),
                      0 68px 19px rgba(0,0,0,0);
  --ds-elevation-100-filter: drop-shadow(0.5px 0.5px 0.5px rgba(0,0,0,0.08));
  --ds-elevation-100-clip-gutter-block: 2px;

  --ds-input-shadow: 0 0 0.5px 0.5px rgba(0,0,0,0.1),
                     0.5px 0.5px 1px rgba(0,0,0,0.18);
  --ds-input-shadow-inset: inset 0.5px 0.5px 0.5px 0 white;
  --ds-input-shadow-hover: 0 0 0.5px 0.5px rgba(0,0,0,0.04),
                           0 0 0 0.5px color-mix(in srgb, var(--ds-brand-primary) 24%, transparent),
                           0.5px 0.5px 1px rgba(0,0,0,0.08);
  --ds-input-shadow-focus: 0 0 0.5px 0.5px rgba(0,0,0,0.04),
                           0 0 0 3px color-mix(in srgb, var(--ds-brand-primary) 10%, transparent),
                           0.5px 0.5px 1px rgba(0,0,0,0.08);

  /* ===== L2 动效 ===== */
  --ds-duration-fast: 180ms;
  --ds-duration-base: 240ms;
  --ds-duration-slow: 350ms;
  --ds-duration-layout: 500ms;
  --ds-duration-bounce: 700ms;

  --ds-ease-swift: cubic-bezier(0.33, 1, 0.68, 1);
  --ds-ease-soft: cubic-bezier(0.22, 1, 0.36, 1);
  --ds-ease-spring: cubic-bezier(0.34, 1.3, 0.64, 1);
  --ds-ease-spring-gentle: cubic-bezier(0.34, 1.15, 0.64, 1);
  --ds-ease-bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55);

  --ds-motion-swift: var(--ds-duration-fast) var(--ds-ease-swift);
  --ds-motion-soft: var(--ds-duration-base) var(--ds-ease-soft);
  --ds-motion-spring: var(--ds-duration-base) var(--ds-ease-spring);
  --ds-motion-layout: var(--ds-duration-layout) var(--ds-ease-soft);
  --ds-motion-bounce: var(--ds-duration-bounce) var(--ds-ease-bounce);

  /* ===== 字体 ===== */
  --ds-font-sans: 'Inter Variable', Inter, 'Noto Sans SC Variable',
                  ui-sans-serif, system-ui, -apple-system, 'PingFang SC',
                  'Microsoft YaHei', sans-serif;
  --ds-font-serif: 'Noto Serif SC Variable', Georgia, 'Songti SC', serif;
  --ds-font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  --ds-font-kbd: 'Inter Variable', 'Inter', sans-serif;
  --ds-font-display-number: 'Spline Sans Mono', ui-monospace, monospace;

  /* ===== 间距（4px 栅格）===== */
  --space-0-5: 2px;
  --space-1: 4px;
  --space-1-5: 6px;
  --space-2: 8px;
  --space-2-5: 10px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
}

/* ===== 暗色覆盖 ===== */
.dark {
  --ds-text-primary: rgba(255,255,255,0.9);
  --ds-text-secondary: rgba(255,255,255,0.5);
  --ds-text-tertiary: rgba(255,255,255,0.38);

  --ds-brand-primary: #36d385;
  --ds-brand-primary-text: rgba(0,0,0,0.9);
  --ds-accent: #36d385;
  --ds-accent-text: rgba(0,0,0,0.9);
  --ds-glow-rgb: 58,146,102;

  --ds-danger: #e56555;
  --ds-success: #4ade80;
  --ds-warning: #e0a830;
  --ds-danger-bg: color-mix(in srgb, var(--ds-danger) 12%, var(--ds-surface-100));
  --ds-success-bg: color-mix(in srgb, var(--ds-success) 12%, var(--ds-surface-100));
  --ds-warning-bg: color-mix(in srgb, var(--ds-warning) 12%, var(--ds-surface-100));

  --ds-surface-100: #111212;
  --ds-surface-200: #171818;
  --ds-surface-300: #232524;
  --ds-surface-400: #272a28;
  --ds-surface-input: var(--ds-surface-300);
  --ds-on-surface: rgba(168,184,176,0.08);
  --ds-on-surface-active: rgba(168,184,176,0.16);
  --ds-divider: color-mix(in srgb, var(--ds-text-primary) 12%, transparent);
  --ds-overlay: rgba(0,0,0,0.6);
  --ds-popover: var(--ds-surface-300);
  --ds-menu-bg: var(--ds-surface-400);

  --ds-elevation-100: 0 0 0 0.5px rgba(255,255,255,0.08);
  --ds-elevation-100-filter: none;
  --ds-elevation-100-clip-gutter-block: 1px;
  --ds-elevation-200: inset 0 0 0 0.5px rgba(255,255,255,0.12),
                      0 0 9px -4px rgba(0,0,0,0.40),
                      0 0 32px -8px rgba(0,0,0,0.16);
  --ds-elevation-300: inset 0 0 0 0.5px rgba(255,255,255,0.12),
                      0 2px 12px -6px rgba(0,0,0,0.44),
                      0 5px 36px -12px rgba(0,0,0,0.20);

  --ds-input-shadow: 0 0 0 0.5px rgba(255,255,255,0.12);
  --ds-input-shadow-inset: none;
  --ds-input-shadow-hover: 0 0 0 0.5px rgba(255,255,255,0.16);
  --ds-input-shadow-focus: 0 0 1px 0.5px color-mix(in srgb, var(--ds-brand-primary) 56%, transparent),
                           0 0 0 4px color-mix(in srgb, var(--ds-brand-primary) 10%, transparent);
}
```

---

## 附录 B：README 级速查

**改一个变量能影响什么**

| 变量 | 效果 |
|---|---|
| `--ds-corner-radius-scale` | 全局圆角性格：0.25 方正 / 1 标准 / 2.5 圆润 |
| `--ds-brand-primary` | 全局品牌色（按钮、链接、焦点环、选中态）|
| `--ds-surface-200` | 应用底色 |
| `--ds-duration-base` | 全局动效节奏 |
| `.dark` 类切换 | 整套亮/暗 |

**最小可用产物**

想让一个新的产品立刻拥有这套质感，只需：
1. 复制附录 A 的 token 块
2. 定两件事：品牌色（亮色 + 暗色各一个）、12 套主题里选基调
3. 组件层全部引用 token

无需拷贝任何组件代码。

---

*本规范提炼自 NewMax AI 1.1.25 的实测设计系统（132 token / 3,166 规则 / 79 关键帧 / 496 个 squircle 元素），
去品牌化后通用化。全部数值可直接使用。*