export const localProfileKey = "studio:local-profile:v1";
const imageData = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/;
export function normalizeLocalProfile(value = {}) {
  return { nickname: typeof value.nickname === "string" ? value.nickname.trim().slice(0, 40) || "本地用户" : "本地用户",
    email: typeof value.email === "string" ? value.email.trim().slice(0, 254) : "",
    avatar: typeof value.avatar === "string" && value.avatar.length <= 300000 && imageData.test(value.avatar) ? value.avatar : "" };
}
export function loadLocalProfile(storage = localStorage) {
  try { return normalizeLocalProfile(JSON.parse(storage.getItem(localProfileKey) || "{}")); }
  catch { return normalizeLocalProfile(); }
}
export function saveLocalProfile(value, storage = localStorage) {
  if (!value.nickname?.trim() || value.nickname.trim().length > 40) throw new Error("昵称须为 1～40 个字符。");
  if (value.email?.trim() && (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.email.trim()) || value.email.trim().length > 254)) throw new Error("请输入有效邮箱地址。");
  const normalized = normalizeLocalProfile(value);
  if (value.avatar && !normalized.avatar) throw new Error("头像格式不支持，请重新选择图片。");
  try { storage.setItem(localProfileKey, JSON.stringify(normalized)); }
  catch { throw new Error("资料保存失败，本地存储不可用或空间不足。"); }
  return normalized;
}

export async function avatarFromFile(file, { decode = createImageBitmap, canvasFactory = () => document.createElement("canvas") } = {}) {
  if (!file || !file.size || file.size > 5 * 1024 * 1024) throw new Error("请选择 5 MB 以内的头像图片。");
  const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const png = [137,80,78,71,13,10,26,10].every((value, index) => bytes[index] === value);
  const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  const webp = String.fromCharCode(...bytes.slice(0,4)) === "RIFF" && String.fromCharCode(...bytes.slice(8,12)) === "WEBP";
  if (!png && !jpeg && !webp) throw new Error("头像支持 PNG、JPG 和 WebP 图片。");
  let bitmap;
  try {
    bitmap = await decode(file);
    if (!(bitmap.width > 0 && bitmap.height > 0)) throw new Error("图片尺寸无效");
    const canvas = canvasFactory(); canvas.width = canvas.height = 256;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法创建图片预览");
    const side = Math.min(bitmap.width, bitmap.height);
    context.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, 256, 256);
    const result = canvas.toDataURL("image/webp", .88);
    if (result.length > 300000 || !imageData.test(result)) throw new Error("图片转换失败");
    return result;
  } catch { throw new Error("无法读取这张图片，请选择有效的 PNG、JPG 或 WebP 文件。"); }
  finally { bitmap?.close?.(); }
}
