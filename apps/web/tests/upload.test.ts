import assert from "node:assert/strict";
import { test } from "node:test";

import { checkUpload, fileBadge, suffixOf } from "../src/lib/upload.ts";

/** Node 没有浏览器 File 的所有行为，这里只需要 name / size 两个字段 */
function fakeFile(name: string, size = 0): File {
  return { name, size } as unknown as File;
}

test("图片后缀按 image 放行", () => {
  assert.deepEqual(checkUpload(fakeFile("board.PNG", 100)), {
    ok: true,
    kind: "image",
  });
});

test("文档后缀按 document 放行", () => {
  assert.deepEqual(checkUpload(fakeFile("讲义.PDF", 100)), {
    ok: true,
    kind: "document",
  });
});

test("未知后缀与无扩展名给出可读拒绝原因", () => {
  const unknown = checkUpload(fakeFile("笔记.exe"));
  assert.equal(unknown.ok, false);
  assert.match(unknown.message, /不支持 \.exe/);

  const noExt = checkUpload(fakeFile("笔记"));
  assert.equal(noExt.ok, false);
  assert.match(noExt.message, /没有扩展名/);
});

test("大小上限与后端一致：文档 20MB / 图片 12MB", () => {
  const bigDoc = checkUpload(fakeFile("书.pdf", 20 * 1024 * 1024 + 1));
  assert.equal(bigDoc.ok, false);
  assert.match(bigDoc.message, /文档上限 20MB/);

  const bigImage = checkUpload(fakeFile("照片.jpg", 12 * 1024 * 1024 + 1));
  assert.equal(bigImage.ok, false);
  assert.match(bigImage.message, /图片上限 12MB/);

  assert.equal(checkUpload(fakeFile("刚好.pdf", 20 * 1024 * 1024)).ok, true);
});

test("徽标取大写后缀", () => {
  assert.equal(suffixOf("a.b.Md"), ".md");
  assert.equal(suffixOf("无后缀"), "");
  assert.equal(fileBadge("讲义.docx"), "DOCX");
  assert.equal(fileBadge("无后缀"), "DOC");
});
