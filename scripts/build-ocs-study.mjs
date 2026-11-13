import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "desktop/package.json"));
const { parse } = require("@babel/parser");
const { transformSync } = require("esbuild");
const vendor = path.join(root, "third_party/ocs/upstream");
const fragments = [], modules = {};
const walk = (node, visit) => {
  if (!node || typeof node !== "object") return;
  if (node.type) visit(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === "object") walk(value, visit);
  }
};
for (const name of ["cx", "zhs", "icourse", "icve", "zjy", "yuketang"]) {
  const source = fs.readFileSync(path.join(vendor, "projects", name + ".ts"), "utf8");
  modules[name] = { source, ast: parse(source, { sourceType: "module", plugins: ["typescript"] }) };
}
function fragment(name, node) {
  fragments.push({ file: "projects/" + name + ".ts", from_line: node.loc.start.line, to_line: node.loc.end.line });
  return modules[name].source.slice(node.start, node.end);
}
function first(name, predicate) {
  let result;
  walk(modules[name].ast.program, node => { if (!result && predicate(node)) result = node; });
  if (!result) throw new Error("Upstream fragment not found: " + name);
  return result;
}
const parts = ["const $el=(selector,root=document)=>root.querySelector(selector); const $$el=(selector,root=document)=>Array.from(root.querySelectorAll(selector));"];
const cxRunner = first("cx", node => node.type === "VariableDeclarator" && node.id?.name === "JobRunner");
const readerNames = new Set(["read", "readPPTWithAudio", "hyperlink"]);
const readers = cxRunner.init.properties.filter(method => readerNames.has(method.key?.name));
if (readers.length !== readerNames.size) throw new Error("Incomplete upstream Chaoxing reader handlers");
parts.push("const $={sleep:ms=>window.__studioLearning.delay(ms)}; const cxReaders={" + readers.map(method => fragment("cx", method)).join(",\n") + "};");
const pureMethods = new Set(["getNext", "hasJob", "getChapterName", "getCourseName"]);
const classes = modules.zhs.ast.program.body.filter(node => node.type === "ClassDeclaration");
for (const node of classes) {
  parts.push("class " + node.id.name + (node.superClass ? " extends " + node.superClass.name : "") + " {" + node.body.body.filter(method => pureMethods.has(method.key?.name)).map(method => fragment("zhs", method)).join("\n") + "}");
}
const pureFunctions = ["getUniqueCourseId", "isZyk", "getVueBindElement", "getPPTVueBindElement", "getCourseDataInClassroomNowPage"];
for (const name of pureFunctions) parts.push(fragment("zjy", first("zjy", node => node.type === "FunctionDeclaration" && node.id?.name === name)));
const zjyList = first("zjy", node => node.type === "VariableDeclarator" && node.id?.name === "getDataList");
parts.push("const zjyDataList=" + fragment("zjy", zjyList.init) + ";");
const icourseNext = first("icourse", node => node.type === "FunctionDeclaration" && node.id?.name === "next");
parts.push(fragment("icourse", icourseNext).replace("function next(", "function icourseNext("));
const v2Next = first("icourse", node => node.type === "FunctionDeclaration" && node.id?.name === "next" && node.loc.start.line > 500);
const getters = ["getCurrentChapter","getCurrentLesson","getCurrentUnit"].map(name => { const node = first("icourse", item => item.type === "VariableDeclarator" && item.id?.name === name); return "const "+name+"="+fragment("icourse",node.init)+";"; }).join("\n");
parts.push("async function icourseNextV2(remotePage){"+getters+"return ("+fragment("icourse",v2Next)+")();}");
const finished = first("icourse", node => node.type === "VariableDeclarator" && node.id?.name === "isJobFinish");
parts.push("const icourseFinished=" + fragment("icourse", finished.init) + ";");
for (const name of ["getJobs", "getJobName", "getJobTag"]) {
  const node = first("yuketang", item => item.type === "VariableDeclarator" && item.id?.name === name);
  parts.push("const " + name + "=" + fragment("yuketang", node.init) + ";");
}
const yktNext = first("yuketang", node => node.type === "VariableDeclarator" && node.id?.name === "getNextJob");
parts.push("function yktNext(){return (" + fragment("yuketang", yktNext.init) + ")();}");
const icveLinks = first("icve", node => node.type === "AssignmentExpression" && modules.icve.source.slice(node.right.start, node.right.end).includes("document.querySelectorAll<HTMLAnchorElement>('.h_cells a')"));
parts.push("const icveDirectoryUrls=()=>" + fragment("icve", icveLinks.right) + ";");
parts.push("window.__studioOCSUpstream={version:'ocs-5.4.0-local.2',cx:cxReaders,zhs:{" + classes.map(node => node.id.name).join(",") + "},zjy:{getUniqueCourseId,isZyk,getVueBindElement,getPPTVueBindElement,getCourseDataInClassroomNowPage,zjyDataList},icourse:{next:icourseNext,nextV2:icourseNextV2,finished:icourseFinished},icve:{directoryUrls:icveDirectoryUrls},yuketang:{getJobs,getJobName,getJobTag,next:yktNext}};");
const output = transformSync("(()=>{if(window.__studioOCSUpstream?.version==='ocs-5.4.0-local.2')return;\n" + parts.join("\n") + "\n})();", { loader: "ts", target: "chrome110", legalComments: "none" }).code;
const destination = path.join(root, "backend/app/services/learning/ocs-study.js");
fs.writeFileSync(destination, "/* OCS 5.4.0, MIT, Copyright (c) 2022 enncy. Generated by scripts/build-ocs-study.mjs; see OCS-LICENSE. */\n" + output);
const hashes = Object.fromEntries(Object.entries(modules).map(([name, { source }]) => ["projects/" + name + ".ts", crypto.createHash("sha256").update(source).digest("hex")]));
fs.writeFileSync(path.join(root, "third_party/ocs/upstream-manifest.json"), JSON.stringify({ repository: "https://github.com/ocsjs/ocsjs", commit: "1e93399a22b54fac80bb6bae131a4b208b5d8af9", version: "5.4.0", license: "MIT", source_hashes: hashes, extracted_fragments: fragments, runtime_sha256: crypto.createHash("sha256").update(fs.readFileSync(destination)).digest("hex") }, null, 2) + "\n");
console.log("Generated local OCS study helpers: " + fragments.length + " source fragments");
