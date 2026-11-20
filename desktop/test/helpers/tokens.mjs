import { readFile } from "node:fs/promises";

const source = new URL("../../src/styles/tokens.css", import.meta.url);

function block(css, opener) {
  const start = css.indexOf(opener);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    if (css[index] === "}" && --depth === 0) return css.slice(open + 1, index);
  }
  throw new Error(`未找到 ${opener}`);
}

function declarations(text) {
  const map = new Map();
  for (const match of text.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) map.set(match[1], match[2].trim());
  return map;
}

export async function loadTokens() {
  const css = await readFile(source, "utf8");
  const light = declarations(block(css, ":root {"));
  const dark = new Map([...light, ...declarations(block(css, "html.dark {"))]);
  return { css, light, dark, lightOverrides: light, darkOverrides: declarations(block(css, "html.dark {")) };
}

const splitArgs = text => {
  const parts = [];
  let depth = 0, current = "";
  for (const char of text) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === "," && depth === 0) { parts.push(current.trim()); current = ""; } else current += char;
  }
  parts.push(current.trim());
  return parts;
};

/** 把 token 解析为 [r, g, b, a]，覆盖 hex、rgba、var、color-mix 四种写法。 */
export function resolve(name, map, over = [255, 255, 255]) {
  const parse = raw => {
    const value = raw.trim();
    const variable = value.match(/^var\((--[a-z0-9-]+)\)$/);
    if (variable) return parse(map.get(variable[1]));
    if (value === "transparent") return [0, 0, 0, 0];
    const hex = value.match(/^#([0-9a-f]{6})$/i);
    if (hex) return [0, 2, 4].map(i => parseInt(hex[1].slice(i, i + 2), 16)).concat(1);
    if (value === "white") return [255, 255, 255, 1];
    const rgba = value.match(/^rgba?\((.+)\)$/);
    if (rgba) {
      const args = splitArgs(rgba[1]).map(item => item.trim());
      if (args[0].startsWith("var(")) {
        const channels = parse(`rgb(${map.get(args[0].slice(4, -1))})`);
        return [channels[0], channels[1], channels[2], Number(args[1])];
      }
      const parts = args.join(",").split(/[\s,]+/).map(Number);
      return [parts[0], parts[1], parts[2], parts[3] ?? 1];
    }
    const mix = value.match(/^color-mix\(in (?:srgb|oklch),\s*(.+)\)$/);
    if (mix) {
      const [first, second] = splitArgs(mix[1]);
      const read = text => {
        const m = text.match(/^(.*?)(?:\s+(var\(--[a-z0-9-]+\)|\d+(?:\.\d+)?%))?$/);
        const raw = m[2]?.startsWith("var(") ? map.get(m[2].slice(4, -1)) : m[2];
        return { color: parse(m[1] || text), weight: raw === undefined ? null : parseFloat(raw) / 100 };
      };
      const a = read(first), b = read(second);
      const wa = a.weight ?? (b.weight === null ? .5 : 1 - b.weight);
      const wb = b.weight ?? 1 - wa;
      const alpha = a.color[3] * wa + b.color[3] * wb;
      const channel = i => (a.color[i] * a.color[3] * wa + b.color[i] * b.color[3] * wb) / (alpha || 1);
      return [channel(0), channel(1), channel(2), alpha];
    }
    throw new Error(`无法解析 ${name}: ${raw}`);
  };
  const [r, g, b, a] = parse(`var(${name})`);
  return [0, 1, 2].map(i => [r, g, b][i] * a + over[i] * (1 - a));
}

const linear = value => { const c = value / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
const luminance = ([r, g, b]) => .2126 * linear(r) + .7152 * linear(g) + .0722 * linear(b);
export function contrast(a, b) {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
