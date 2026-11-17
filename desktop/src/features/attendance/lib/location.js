export function normalizeLocationReading(value, fallbackSource = "browser") {
  const latitude = Number(value?.latitude ?? value?.coords?.latitude);
  const longitude = Number(value?.longitude ?? value?.coords?.longitude);
  const accuracy = Number(value?.accuracy ?? value?.coords?.accuracy ?? 0);
  if (
    !Number.isFinite(latitude)
    || !Number.isFinite(longitude)
    || Math.abs(latitude) > 90
    || Math.abs(longitude) > 180
  ) {
    throw new Error("定位结果无效");
  }
  return {
    latitude,
    longitude,
    accuracy: Number.isFinite(accuracy) && accuracy >= 0 ? accuracy : 0,
    source: fallbackSource === "windows" ? "browser" : fallbackSource,
  };
}

function browserPosition(geolocation, options) {
  return new Promise((resolve, reject) => {
    geolocation.getCurrentPosition(
      resolve,
      cause => {
        const code = Number(cause?.code) || 2;
        const error = new Error(
          code === 1
            ? "定位权限被拒绝，请在系统设置中允许 Mumu 使用位置"
            : code === 3
              ? "定位超时，请重试"
              : "系统定位服务不可用，请检查系统权限或手动输入位置",
        );
        error.code = code;
        reject(error);
      },
      options,
    );
  });
}

export async function readCurrentAttendanceLocation({
  desktopApi = globalThis.window?.desktopApi,
  geolocation = globalThis.navigator?.geolocation,
} = {}) {
  let nativeError = null;
  if (desktopApi?.getWindowsLocation) {
    try {
      return normalizeLocationReading(await desktopApi.getWindowsLocation(), "windows");
    } catch (cause) {
      nativeError = cause;
    }
  }
  if (!geolocation?.getCurrentPosition) {
    const error = new Error(
      nativeError?.message || "当前环境不支持定位",
    );
    error.code = 2;
    throw error;
  }
  try {
    return normalizeLocationReading(await browserPosition(geolocation, {
      enableHighAccuracy: true,
      timeout: 12000,
      maximumAge: 0,
    }));
  } catch (firstError) {
    if (firstError.code === 1) throw firstError;
    try {
      return normalizeLocationReading(await browserPosition(geolocation, {
        enableHighAccuracy: false,
        timeout: 15000,
        maximumAge: 60000,
      }));
    } catch (finalError) {
      if (nativeError && finalError.code !== 3) {
        finalError.message = "Windows 与浏览器定位均不可用，请检查定位设置";
      }
      throw finalError;
    }
  }
}
