import { artifactUrl } from "./backend-url.js";

export function splitScreenshot(width, height, pageHeight = 1000, overlap = 64) {
  if (!(width > 0 && height > 0 && pageHeight > overlap && overlap >= 0)) return [];
  const pages = [];
  for (let y = 0; y < height; y += pageHeight - overlap) {
    pages.push({ y, width, height: Math.min(pageHeight, height - y) });
    if (y + pageHeight >= height) break;
  }
  return pages;
}

export function readableScreenshotZoom(width, viewportWidth) {
  if (!(width > 0 && viewportWidth > 0)) return 100;
  return Math.max(100, Math.min(250, Math.ceil(Math.min(width, 1280) / viewportWidth * 4) * 25));
}

export function finalScreenshotSections(events, artifacts) {
  const lastCapture = [...events].reverse().find(event => event.kind === "final_screenshot");
  return (lastCapture?.data?.segments || []).map(section => {
    const artifact = artifacts.find(item => item.id === section.artifact_id);
    return artifact ? { ...section, id: artifact.id, src: artifactUrl(artifact.id) } : null;
  }).filter(Boolean);
}
