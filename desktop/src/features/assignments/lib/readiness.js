export function requiresMaterials(assignment) {
  return assignment?.assessment?.execution_state === "manual_required";
}

export function directlyAnswerable(assignment) {
  const assessment = assignment?.assessment || {};
  if (assignment?.submittable === false || assignment?.submittable === 0 || requiresMaterials(assignment) || assessment.requirements?.length) return false;
  return (assessment.runtime === "educoder" && assessment.execution_state === "code_experiment" && assessment.unit_count > 0)
    || (assessment.policy_version === 3 && assessment.execution_state === "direct_fill" && assessment.automation_supported === true);
}

export function assignmentProcessingReason(assignment) {
  if (directlyAnswerable(assignment)) return "";
  const assessment = assignment?.assessment || {};
  if (assessment.requirements?.length) return '需要' + assessment.requirements.join('、');
  return assessment.reason || assessment.blocked_units?.[0]?.reason
    || (assignment?.submittable === false || assignment?.submittable === 0 ? '平台当前没有开放的作答入口' : '尚未确认可直接完成，请重新检查');
}

export function assignmentReadiness(assignment) {
  const assessment = assignment?.assessment || {};
  if (assessment.runtime === "educoder") {
    return { label: String(assessment.filled_units || 0) + '/' + assessment.unit_count + ' 关已通过', className: assessment.answer_state === 'passed' ? 'filled' : 'partial', detail: assessment.availability === 'unknown' ? '开放状态待核验' : '' };
  }
  if (requiresMaterials(assignment)) {
    const required = assessment.requirements || [];
    return {
      label: required.length ? "需材料" : "需人工处理",
      className: "materials",
      detail: required.join("、") || assignmentProcessingReason(assignment),
    };
  }
  if (assessment.answer_state === "empty") return { label: "待作答", className: "empty", detail: "" };
  if (assessment.answer_state === "partial") return { label: "部分填写", className: "partial", detail: assessment.filled_units + "/" + assessment.unit_count + " 个作答单元已填写" };
  if (assessment.answer_state === "filled") return { label: "已填写，待核验", className: "filled", detail: "" };
  return { label: "待检查", className: "unknown", detail: "" };
}
