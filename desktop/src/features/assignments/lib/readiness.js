export function requiresMaterials(assignment) {
  return assignment?.assessment?.execution_state === "manual_required";
}

export function directlyAnswerable(assignment) {
  const assessment = assignment?.assessment || {};
  return assessment.runtime === "educoder" || assessment.automation_supported === true;
}

export function assignmentReadiness(assignment) {
  const assessment = assignment?.assessment || {};
  if (assessment.runtime === "educoder") {
    return { label: String(assessment.filled_units || 0) + '/' + assessment.unit_count + ' 关已通过', className: assessment.answer_state === 'passed' ? 'filled' : 'partial', detail: assessment.availability === 'unknown' ? '开放状态待核验' : '' };
  }
  if (requiresMaterials(assignment)) {
    const required = assessment.requirements || [];
    const unsupported = assessment.unsupported_types || [];
    return {
      label: required.length ? "需材料" : "需人工处理",
      className: "materials",
      detail: required.join("、") || unsupported.join("、") || "自动处理仅支持选择题和填空题",
    };
  }
  if (assessment.unsupported_types?.length && assessment.automation_supported === false) {
    return { label: "仅手动处理", className: "materials", detail: assessment.unsupported_types.join("、") };
  }
  if (assessment.answer_state === "empty") return { label: "待作答", className: "empty", detail: "" };
  if (assessment.answer_state === "partial") return { label: "部分填写", className: "partial", detail: assessment.filled_units + "/" + assessment.unit_count + " 个作答单元已填写" };
  if (assessment.answer_state === "filled") return { label: "已填写，待核验", className: "filled", detail: "" };
  return { label: "待检查", className: "unknown", detail: "" };
}
