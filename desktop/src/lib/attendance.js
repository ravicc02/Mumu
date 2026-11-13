export const attendanceKinds = { normal: '普通', qr: '二维码', code: '签到码', gesture: '手势码', location: '位置', unknown: '待核验' };
export const attendanceStates = { pending: '待执行', queued: '排队中', running: '核验中', submitting: '提交中', succeeded: '已签到', already_signed: '已签到', awaiting_input: '待补充', failed: '失败', auth_required: '待授权', expired: '已结束', manual_required: '需本人处理', unknown: '待核对', cancelled: '已取消' };
export const attendanceRunning = batch => Boolean(batch?.items.some(item => ['queued', 'running', 'submitting'].includes(item.status)));
export const attendanceRunnable = batch => Boolean(batch && !attendanceRunning(batch) && !batch.items.some(item => item.status === 'unknown') && batch.items.some(item => ['pending', 'awaiting_input', 'failed', 'auth_required', 'cancelled'].includes(item.status)));

export function attendanceWindowOpen(item, now = Date.now()) {
  if (item.availability_status !== 'open' || !item.verified_at) return false;
  const start = Number(item.start_time);
  if (!Number.isFinite(start) || start <= 0 || start * 1000 > now) return false;
  if (item.end_time == null) return item.open_ended === true || item.open_ended === 1;
  const end = Number(item.end_time);
  return Number.isFinite(end) && end > start && end * 1000 > now;
}

export function attendanceWindowLabel(item) {
  const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  const start = Number(item.start_time), end = Number(item.end_time);
  if (!Number.isFinite(start) || start <= 0) return '';
  return formatter.format(new Date(start * 1000)) + ' 至 ' + (item.end_time == null ? '平台未设截止' : Number.isFinite(end) ? formatter.format(new Date(end * 1000)) : '时间待核实');
}

export function attendanceLocation(latitude, longitude, address, source = 'manual') {
  if (latitude === '' && longitude === '' && !address.trim()) return null;
  const lat = Number(latitude), lon = Number(longitude);
  if (latitude === '' || longitude === '' || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    throw new Error('请填写有效的纬度（-90 至 90）和经度（-180 至 180）');
  }
  if (!address.trim() || address.trim().length > 200) throw new Error('请填写不超过 200 字的实际位置名称');
  return { latitude: lat, longitude: lon, address: address.trim(), source };
}

export async function readAttendanceQr(file) {
  if (!file || file.size > 4 * 1024 * 1024) throw new Error('请选择不超过 4 MB 的二维码图片');
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 1800 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const image = context.getImageData(0, 0, canvas.width, canvas.height);
    const { default: jsQR } = await import('jsqr');
    const code = jsQR(image.data, image.width, image.height);
    if (!code?.data) throw new Error('图片中未识别到二维码');
    return code.data;
  } finally { bitmap.close(); }
}
