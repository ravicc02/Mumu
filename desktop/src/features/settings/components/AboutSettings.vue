<script setup>
import { ref } from "vue";
import { ArrowUpRight, FileText, FolderOpen, LoaderCircle, RefreshCw, Scale } from "@lucide/vue";
import { appInfo, displayVersion } from "../../../app/lib/product.js";

const props = defineProps({
  update: { type: Object, required: true },
  checkUpdates: { type: Function, required: true },
  openExternal: { type: Function, required: true },
  openLogs: { type: Function, required: true },
});

const error = ref("");
const platformLabel = globalThis.desktopApi?.platform === "linux" ? "Linux 桌面端" : "Windows 桌面端";

async function openLink(url) {
  error.value = "";
  try {
    await props.openExternal(url);
  } catch (failure) {
    error.value = failure.message;
  }
}
</script>

<template>
  <div class="about-settings">
    <article class="about-sheet">
      <header class="about-masthead">
        <div class="about-brand-lockup">
          <span class="about-mark"><img src="/brand/mumu.png" width="64" height="64" alt="Mumu" /></span>
          <div class="about-brand-copy">
            <span class="about-platform">{{ platformLabel }}</span>
            <h3>{{ appInfo.name }}</h3>
            <p>面向学生任务执行与课程学习的本地工作区</p>
          </div>
        </div>
        <div class="about-version">
          <span>当前版本</span>
          <strong>{{ displayVersion }}</strong>
        </div>
      </header>

      <dl class="about-facts">
        <div>
          <dt>项目主页</dt>
          <dd>
            <button class="text-button" type="button" @click="openLink(appInfo.repository)">
              github.com/ravicc02/Mumu<ArrowUpRight :size="13" />
            </button>
          </dd>
        </div>
        <div>
          <dt>运行方式</dt>
          <dd>本地桌面服务</dd>
        </div>
      </dl>

      <section class="about-update-section">
        <div class="about-update-heading">
          <div>
            <h4>版本更新</h4>
            <p>启动时读取 GitHub 发布信息，不上传本地数据</p>
          </div>
        </div>

        <div class="about-update-status-row">
          <div class="about-update-copy" :class="{ error: update.status === 'error', success: update.status === 'current' }" role="status">
            <span class="about-status-dot" aria-hidden="true" />
            <div>
              <strong>{{ update.status === 'available' ? '发现可用更新' : update.status === 'current' ? '当前已是最新版本' : update.status === 'checking' ? '正在检查更新' : '准备检查发布状态' }}</strong>
              <p>{{ update.message || '尚未检查已发布版本' }}</p>
            </div>
          </div>
          <div class="about-update-actions">
            <time v-if="update.checkedAt">上次检查 {{ new Date(update.checkedAt).toLocaleString('zh-CN', { hour12: false }) }}</time>
            <button v-if="update.status === 'available'" class="text-button" type="button" @click="openLink(update.url)">查看新版本<ArrowUpRight :size="13" /></button>
            <button class="button dark" type="button" :disabled="update.status === 'checking'" @click="checkUpdates">
              <LoaderCircle v-if="update.status === 'checking'" class="spinning" :size="15" />
              <RefreshCw v-else :size="15" />{{ update.status === 'checking' ? '正在检查' : '检查更新' }}
            </button>
          </div>
        </div>
      </section>

      <footer class="about-footer">
        <button type="button" @click="openLink(appInfo.repository + '/releases')">
          <FileText :size="15" />更新记录<ArrowUpRight :size="13" />
        </button>
        <button type="button" title="查看 GPL-3.0-only 许可证" @click="openLink(appInfo.repository + '/blob/main/LICENSE')">
          <Scale :size="15" />GPL-3.0-only · 无担保<ArrowUpRight :size="13" />
        </button>
        <button type="button" @click="openLogs">
          <FolderOpen :size="15" />日志目录<ArrowUpRight :size="13" />
        </button>
      </footer>
    </article>

    <p v-if="error" class="about-error validation-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
/* finesse · register=product · shell=product-colophon · layout=single-sheet
 * engine=css-feedback · SOUL=5 SPECTACLE=1 DENSITY=5 */
.about-settings {
  width: 100%;
  max-width: 900px;
}

.about-sheet {
  overflow: hidden;
  border: 1px solid rgba(24, 33, 44, .11);
  border-radius: 14px;
  background: #fdfefe;
  box-shadow: 0 1px 3px rgba(20, 30, 42, .04);
}

.about-masthead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  min-height: 168px;
  border-bottom: 1px solid var(--line);
  background: #f7f9fb;
  padding: 28px 32px;
}

.about-brand-lockup {
  display: flex;
  align-items: center;
  gap: 20px;
  min-width: 0;
}

.about-mark {
  display: grid;
  place-items: center;
  width: 88px;
  height: 88px;
  flex: none;
  border: 1px solid rgba(24, 33, 44, .10);
  border-radius: 18px;
  background: #fdfefe;
}

.about-brand-copy {
  min-width: 0;
}

.about-platform {
  color: var(--accent);
  font-size: 11px;
}

.about-brand-copy h3 {
  margin-top: 3px;
  color: var(--ink);
  font-size: 32px;
  line-height: 1.1;
  font-weight: 500;
}

.about-brand-copy p {
  margin-top: 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

.about-version {
  display: grid;
  justify-items: end;
  gap: 3px;
  flex: none;
}

.about-version span {
  color: var(--muted);
  font-size: 11px;
}

.about-version strong {
  color: var(--ink);
  font-size: 28px;
  line-height: 1.1;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.about-facts {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(180px, .75fr);
  margin: 0;
  border-bottom: 1px solid var(--line);
}

.about-facts > div {
  display: grid;
  gap: 6px;
  padding: 16px 32px;
}

.about-facts > div + div {
  border-left: 1px solid var(--line);
}

.about-facts dt {
  color: var(--muted);
  font-size: 11px;
}

.about-facts dd {
  min-width: 0;
  margin: 0;
  color: var(--ink);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.about-facts .text-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  color: var(--accent);
  font-size: 13px;
}

.about-update-section {
  padding: 24px 32px 26px;
}

.about-update-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 16px;
}

.about-update-heading h4 {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 500;
}

.about-update-heading p {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.about-update-status-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 24px;
  padding-top: 18px;
}

.about-update-copy {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
  color: var(--muted);
}

.about-status-dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
  background: var(--subtle);
  margin-top: 6px;
}

.about-update-copy.success .about-status-dot {
  background: var(--green);
}

.about-update-copy.error .about-status-dot {
  background: var(--red);
}

.about-update-copy strong {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.about-update-copy p {
  margin-top: 3px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.about-update-copy.error strong,
.about-update-copy.error p {
  color: var(--red);
}

.about-update-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 14px;
}

.about-update-actions time {
  color: var(--subtle);
  font-size: 11px;
}

.about-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 24px;
  min-height: 60px;
  border-top: 1px solid var(--line);
  background: #fbfcfd;
  padding: 12px 32px;
}

.about-footer button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 0;
  background: transparent;
  padding: 4px 0;
  color: var(--muted);
  font-size: 12px;
}

.about-footer button + button {
  border-left: 1px solid var(--line);
  padding-left: 24px;
}

.about-footer button > svg:last-child {
  color: var(--subtle);
}

@media (hover: hover) {
  .about-footer button:hover {
    color: var(--accent);
  }
}

.about-error {
  margin-top: 12px;
  border: 1px solid rgba(182, 65, 59, .18);
  border-radius: 8px;
  background: var(--red-soft);
  padding: 10px 12px;
  font-size: 12px;
}

@media (max-width: 900px) {
  .about-masthead,
  .about-update-heading {
    align-items: flex-start;
  }

  .about-version {
    justify-items: start;
  }

  .about-update-heading {
    flex-direction: column;
  }

  .about-update-status-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .about-update-actions {
    justify-content: flex-start;
  }

  .about-update-actions time {
    margin-right: 0;
  }
}

@media (max-width: 680px) {
  .about-masthead {
    flex-direction: column;
    gap: 20px;
    padding: 24px;
  }

  .about-facts {
    grid-template-columns: minmax(0, 1fr);
  }

  .about-facts > div {
    padding-inline: 24px;
  }

  .about-facts > div + div {
    border-top: 1px solid var(--line);
    border-left: 0;
  }

  .about-update-section {
    padding-inline: 24px;
  }

  .about-footer {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
    padding-inline: 24px;
  }

  .about-footer button + button {
    border-left: 0;
    padding-left: 0;
  }
}
</style>
