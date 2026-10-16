export const SESSION_ARCHIVE_SUFFIX = ".csc-session.zip";
export const MAX_SESSION_ARCHIVE_BYTES = 256 * 1024 * 1024;

export function isArchived(record) {
  return Boolean(record?.archive || record?.archived);
}

export function originLabel(record) {
  return isArchived(record) ? "Archived" : "Live";
}

export function liveSucceededJobs(jobs, workspaceId, kind) {
  return jobs.filter((job) => (
    job.workspace_id === workspaceId
    && job.kind === kind
    && job.status === "succeeded"
    && !isArchived(job)
  ));
}

export function validateSessionArchiveFile(file) {
  if (!file) return "Choose a session archive to import";
  if (!file.name.endsWith(SESSION_ARCHIVE_SUFFIX)) {
    return `Expected a ${SESSION_ARCHIVE_SUFFIX} file`;
  }
  if (file.size > MAX_SESSION_ARCHIVE_BYTES) {
    return "Session archive exceeds the 256 MiB limit";
  }
  return null;
}
