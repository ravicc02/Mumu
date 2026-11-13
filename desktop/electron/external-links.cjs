const product = require("../product.json");
const roots = [product.repository, product.legacyRepository].map(value => new URL(value).pathname.replace(/\/$/, ""));

function allowedProjectLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" && !url.port && !url.username && !url.password && !url.search
      && roots.some(root => url.pathname === root || url.pathname === root + "/releases" || url.pathname.startsWith(root + "/releases/"));
  } catch { return false; }
}
module.exports = { allowedProjectLink };
