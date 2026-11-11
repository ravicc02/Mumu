const fs = require("node:fs");
const path = require("node:path");

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") {
    return;
  }

  const { rcedit } = await import("rcedit");
  const productName = context.packager.appInfo.productFilename;
  const version = context.packager.appInfo.version;
  const executablePath = path.join(context.appOutDir, `${productName}.exe`);
  const iconPath = path.join(context.packager.projectDir, "build", "icon.ico");
  const options = {
    "file-version": version,
    "product-version": version,
    "version-string": {
      CompanyName: "AI Assignment Studio Contributors",
      FileDescription: "AI Assignment Studio",
      InternalFilename: "AI Assignment Studio",
      LegalCopyright: "Copyright AI Assignment Studio Contributors",
      OriginalFilename: "AI Assignment Studio.exe",
      ProductName: "AI Assignment Studio"
    }
  };
  if (fs.existsSync(iconPath)) {
    options.icon = iconPath;
  }

  await rcedit(executablePath, options);
  console.log(`Applied product metadata and icon to ${executablePath}`);
};
