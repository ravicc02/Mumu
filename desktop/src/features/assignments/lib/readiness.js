export function requiresMaterials(assignment) {
  return assignment?.assessment?.execution_state === "manual_required";
}

export function assignmentReadiness(assignment) {
  const assessment = assignment?.assessment || {};
  if (assessment.runtime === "educoder") {
    return { label: String(assessment.filled_units || 0) + '/' + assessment.unit_count + ' 关已通过', className: assessment.answer_state === 'passed' ? 'filled' : 'partial', detail: assessment.availability === 'unknown' ? '开放状态待核验' : '' };
  }
  if (requiresMaterials(assignment)) {
    const required = assessment.requirements || [];
    return { label: required.length ? "需材料" : "需人工处理", className: "materials", detail: required.join("、") || "作答控件暂不支持直接填写" };
  }
  if (assessment.answer_state === "empty") return { label: "待作答", className: "empty", detail: "" };
  if (assessment.answer_state === "partial") return { label: "部分填写", className: "partial", detail: assessment.filled_units + "/" + assessment.unit_count + " 个作答单元已填写" };
  if (assessment.answer_state === "filled") return { label: "已填写，待核验", className: "filled", detail: "" };
  return { label: "待检查", className: "unknown", detail: "" };
}
