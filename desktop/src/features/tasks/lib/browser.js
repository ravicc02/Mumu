export function assignmentBrowserTask(jobs, accountId, selectedId = "") {
  const records = jobs.filter(job => job.account_id === accountId);
  return records.find(job => job.id === selectedId)
    || records.find(job => job.browser_available)
    || records.find(job => ["running", "paused", "queued", "stopping"].includes(job.status))
    || records[0] || null;
}

export function activeAssignmentBrowserTask(job) {
  return job && (job.browser_available || ["running", "paused", "queued", "stopping"].includes(job.status)) ? job : null;
}
