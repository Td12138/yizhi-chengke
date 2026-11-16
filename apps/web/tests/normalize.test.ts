import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clampCount,
  normalizeStreamedCard,
  normalizeStreamedQuestion,
  readReviewResult,
} from "../src/lib/normalize.ts";

test("题数夹到 1–12", () => {
  assert.equal(clampCount(Number.NaN), 1);
  assert.equal(clampCount(0), 1);
  assert.equal(clampCount(99), 12);
  assert.equal(clampCount(5.6), 6);
});

test("流式题目缺字段时补默认值，tmp id 用负数占位", () => {
  const question = normalizeStreamedQuestion({ stem_latex: "$x^2$" }, -1);

  assert.equal(question.id, -1);
  assert.equal(question.stem_latex, "$x^2$");
  assert.equal(question.qtype, "fill");
  assert.deepEqual(question.options, []);
  assert.equal(question.verified, 0);
});

test("流式题目保留真实 id 与选择题选项", () => {
  const question = normalizeStreamedQuestion(
    { id: 7, qtype: "choice", options: [1, "B"], verified: true },
    -2
  );

  assert.equal(question.id, 7);
  assert.equal(question.qtype, "choice");
  assert.deepEqual(question.options, ["1", "B"]);
  assert.equal(question.verified, 1);
});

test("流式卡片把标签数组拼成逗号串", () => {
  const card = normalizeStreamedCard({ title: "顶点式", tags: ["二次函数", 1] }, -3);

  assert.equal(card.id, -3);
  assert.equal(card.title, "顶点式");
  assert.equal(card.tags, "二次函数,1");
  assert.equal(normalizeStreamedCard({}, -4).title, "（识别中）");
});

test("批改结论：score 不是数字视为没拿到结论", () => {
  assert.equal(readReviewResult(null), null);
  assert.equal(readReviewResult({ score: "满分" }), null);
});

test("批改结论：缺字段补空，坏步骤被丢掉", () => {
  const result = readReviewResult({
    score: "80",
    verdict: "partial",
    points: [{ hit: 1, text: "配方正确" }, { hit: 0 }, "bad"],
    steps: [{ expect: "配方" }, { index: -1, expect: "求顶点" }, { expect: 42 }],
    first_error: { step: "2", reason: "符号错" },
  });

  assert.ok(result);
  assert.equal(result.score, 80);
  assert.equal(result.verdict, "partial");
  assert.deepEqual(result.points, [{ hit: true, text: "配方正确" }]);
  assert.deepEqual(result.steps, [
    { index: 1, expect: "配方", found: false, note: "" },
    { index: 2, expect: "求顶点", found: false, note: "" },
  ]);
  assert.deepEqual(result.first_error, { step: 2, reason: "符号错" });
  assert.equal(result.better_method, null);
  assert.deepEqual(result.issues, []);
});

test("流式题目带验算级别，旧后端缺字段补空串", () => {
  assert.equal(
    normalizeStreamedQuestion({ verify_level: "structural" }, -1).verify_level,
    "structural"
  );
  assert.equal(normalizeStreamedQuestion({}, -1).verify_level, "");
});

test("流式卡片带修正时间，缺字段为 null", () => {
  assert.equal(normalizeStreamedCard({ revised_at: 123 }, -1).revised_at, 123);
  assert.equal(normalizeStreamedCard({}, -1).revised_at, null);
});

test("批改结论：方法归因缺名字视为没有归因", () => {
  const withMethod = readReviewResult({
    score: 100,
    student_method: { name: "配方法", valid: true, note: "等价于公式法" },
  });
  assert.deepEqual(withMethod?.student_method, {
    name: "配方法",
    valid: true,
    note: "等价于公式法",
  });

  assert.equal(readReviewResult({ score: 100 })?.student_method, null);
  assert.equal(
    readReviewResult({ score: 100, student_method: { valid: true } })?.student_method,
    null
  );
  assert.equal(
    readReviewResult({ score: 100, student_method: "配方法" })?.student_method,
    null
  );
});
