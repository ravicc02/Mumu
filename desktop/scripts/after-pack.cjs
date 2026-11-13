const fs = require("node:fs");
const path = require("node:path");
const product = require("../product.json");

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") {
    return;
  }

  const { rcedit } = await import("rcedit");
  const productName = context.packager.appInfo.productFilename;
  const version = context.packager.appInfo.version;
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-|$)/.exec(version);
  if (!match) throw new Error("Invalid desktop version: " + version);
  const windowsVersion = match.slice(1, 4).join(".") + ".0";
  const executablePath = path.join(context.appOutDir, `${productName}.exe`);
  const iconPath = path.join(context.packager.projectDir, "build", "icon.ico");
  const options = {
    "file-version": windowsVersion,
    "product-version": windowsVersion,
    "version-string": {
      FileVersion: version,
      ProductVersion: version,
      CompanyName: "Mumu Contributors",
      FileDescription: product.name,
      InternalFilename: productName,
      LegalCopyright: "Copyright Mumu Contributors",
      OriginalFilename: `${productName}.exe`,
      ProductName: product.name
    }
  };
  if (fs.existsSync(iconPath)) {
    options.icon = iconPath;
  }

  const { "version-string": versionStrings, ...fixedMetadata } = options;
  await rcedit(executablePath, fixedMetadata);
  // Fixed version setters overwrite display strings, so restore SemVer last.
  await rcedit(executablePath, { "version-string": versionStrings });
  console.log(`Applied product metadata and icon to ${executablePath}`);
};
