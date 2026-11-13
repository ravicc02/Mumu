function allowedProjectLink(value) {
  try {
    const url = new URL(value), root = "/ravicc02/ai-quiz-assistant";
    return url.protocol === "https:" && url.hostname === "github.com" && !url.port && !url.username && !url.password && !url.search
      && (url.pathname === root || url.pathname === root + "/releases" || url.pathname.startsWith(root + "/releases/"));
  } catch { return false; }
}
module.exports = { allowedProjectLink };
