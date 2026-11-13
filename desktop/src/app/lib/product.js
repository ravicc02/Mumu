import manifest from "../../../package.json" with { type: "json" };
import product from "../../../product.json" with { type: "json" };
export const appInfo = {
  name: product.name,
  version: manifest.version,
  repository: product.repository,
  legacyRepository: product.legacyRepository,
  releaseApi: product.releaseApi,
};
export const displayVersion = appInfo.version.split(/[+-]/)[0];
export const updatePreferenceKey = "studio:updates:v1";
const projectRoots = [appInfo.repository, appInfo.legacyRepository].map(value => new URL(value).pathname.replace(/\/$/, ""));
export function loadUpdatePreference(storage = localStorage) {
  try { return JSON.parse(storage.getItem(updatePreferenceKey) || "{}").auto_check === true; }
  catch { return false; }
}
export function saveUpdatePreference(enabled, storage = localStorage) {
  try { storage.setItem(updatePreferenceKey, JSON.stringify({ auto_check: Boolean(enabled) })); }
  catch { throw new Error("无法保存自动检查更新设置。"); }
}
export function allowedProjectLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" && !url.port && !url.username && !url.password
      && projectRoots.some(root => url.pathname === root || url.pathname === root + "/releases" || url.pathname.startsWith(root + "/releases/"))
      && !url.search;
  } catch { return false; }
}
function stableVersion(value) {
  const match = String(value || "").trim().match(/^v?(\d+)\.(\d+)\.(\d+)$/);
  if (!match) return null;
  return match.slice(1).map(Number);
}
export async function checkPublishedRelease({ currentVersion = appInfo.version, fetcher = fetch, checkedAt = new Date().toISOString() } = {}) {
  const response = await fetcher(appInfo.releaseApi, { headers: { Accept: "application/vnd.github+json" }, credentials: "omit", signal: AbortSignal.timeout(12000) });
  if (response.status === 404) return { status: "unpublished", checkedAt, message: "暂未找到可用的发布版本。" };
  if ([403,429].includes(response.status)) throw new Error("更新检查频率受限，请稍后重试。");
  if (!response.ok) throw new Error("暂时无法获取发布信息，请稍后重试。");
  const release = await response.json(), current = stableVersion(String(currentVersion).split("-")[0]), remote = stableVersion(release.tag_name);
  if (release.draft || release.prerelease || !remote || !current || !allowedProjectLink(release.html_url) || !release.html_url.startsWith(appInfo.repository + "/releases/")) throw new Error("发布信息无法核验，请到项目主页查看。");
  let comparison = 0;
  for (let index = 0; index < 3; index++) { if (remote[index] !== current[index]) { comparison = Math.sign(remote[index] - current[index]); break; } }
  if (!comparison && String(currentVersion).includes("-")) comparison = 1;
  return { status: comparison > 0 ? "available" : "current", checkedAt, version: release.tag_name, url: release.html_url,
    message: comparison > 0 ? "发现新版本 " + release.tag_name : comparison < 0 ? "当前版本高于已发布版本。" : "当前已是最新发布版本。" };
}
