export const API_BASE = import.meta.env?.VITE_API_BASE || "http://127.0.0.1:8877";

export function artifactUrl(id, base = API_BASE) {
  return base.replace(/\/+$/, "") + "/api/artifacts/" + encodeURIComponent(id);
}
