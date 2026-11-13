import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse, compileScript } from "@vue/compiler-sfc";
import { createRenderer, defineComponent, h, nextTick, ref } from "vue";

const { descriptor } = parse(await readFile(new URL("../src/components/ScreenshotViewer.vue", import.meta.url), "utf8"));
let source = compileScript(descriptor, { id: "viewer-test", inlineTemplate: true }).content;
for (const name of ["vue", "@lucide/vue"]) {
  source = source.replaceAll(`from "${name}"`, `from ${JSON.stringify(import.meta.resolve(name))}`);
}
source = source.replace('from "../lib/screenshots"', `from ${JSON.stringify(new URL("../src/lib/screenshots.js", import.meta.url).href)}`);
const { default: Viewer } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

const node = type => ({ type, children: [], props: {}, parent: null });
const renderer = createRenderer({
  createElement: node, createText: text => ({ ...node("text"), text }), createComment: text => ({ ...node("comment"), text }),
  setText: (item, text) => { item.text = text; }, setElementText: (item, text) => { item.text = text; },
  parentNode: item => item.parent, nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1],
  patchProp: (item, key, previous, value) => { item.props[key] = value; },
  insert(item, parent, anchor) {
    if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1);
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item); item.parent = parent;
  },
  remove(item) { item.parent?.children.splice(item.parent.children.indexOf(item), 1); item.parent = null; },
});
const find = (root, predicate) => predicate(root) ? root : root.children.map(item => find(item, predicate)).find(Boolean);

test("viewport captures remain one segment and start fitted to width even at high DPR", async () => {
  const root = node("root");
  const app = renderer.createApp(defineComponent({ setup: () => () => h(Viewer, {
    segments: [{ id: "viewport", index: 1, count: 1, width: 1440, height: 1200, src: "http://local.test/viewport.png" }]
  }) }));
  app.mount(root);
  try {
    find(root, item => item.type === "img").props.onLoad({ target: { naturalWidth: 2880, naturalHeight: 2400 } });
    await nextTick();
    const select = find(root, item => item.type === "select");
    assert.equal(select.children.filter(item => item.type === "option").length, 1);
    assert.equal(find(root, item => item.props.class === "screenshot-page").props.style.width, "100%");
  } finally { app.unmount(); }
});

test("identical artifact polls keep the screenshot loaded and preserve its selected segment", async () => {
  const root = node("root"), sections = ref([{ id: "evidence", src: "http://local.test/one.jpg" }]);
  const app = renderer.createApp(defineComponent({ setup: () => () => h(Viewer, { segments: sections.value }) }));
  app.mount(root);
  try {
    const initial = find(root, item => item.type === "img");
    initial.props.onLoad({ target: { naturalWidth: 1440, naturalHeight: 3000 } });
    await nextTick();
    assert.equal(find(root, item => item.props.role === "status"), undefined);
    find(root, item => item.type === "select").props.onChange({ target: { value: "1" } });
    await nextTick();
    for (let poll = 0; poll < 5; poll++) {
      sections.value = [{ id: "evidence", src: "http://local.test/one.jpg" }];
      await nextTick();
      assert.equal(find(root, item => item.props.role === "status"), undefined);
      assert.equal(find(root, item => item.type === "select").props.value, 1);
    }
    sections.value = [{ id: "evidence", src: "http://local.test/replaced.jpg" }];
    await nextTick();
    assert.ok(find(root, item => item.props.role === "status"));
    assert.equal(find(root, item => item.type === "select").props.value, 0);
  } finally { app.unmount(); }
});
