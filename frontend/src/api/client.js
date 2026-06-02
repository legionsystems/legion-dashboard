const BASE_URL = "/api";

// Thrown on non-2xx responses. Retains the parsed JSON body (if any) so
// callers can inspect structured 409 detail payloads such as the repo
// safety gate's blocker_code/blocker_message.
export class ApiError extends Error {
  constructor({ status, statusText, body, message }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = text; }
    throw new ApiError({
      status: response.status,
      statusText: response.statusText,
      body,
      message: `Request failed: ${response.status} ${response.statusText} ${text}`,
    });
  }
  if (response.status === 204) return null;
  return response.json();
}

export function getJson(path) {
  return request(path, { method: "GET" });
}

export function postJson(path, body) {
  return request(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export function putJson(path, body) {
  return request(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
}

export function deleteRequest(path) {
  return request(path, { method: "DELETE" });
}

export function uploadFile(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);
    xhr.open("POST", `${BASE_URL}${path}`);
    if (onProgress) {
      xhr.upload.addEventListener("progress", onProgress);
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch { resolve(xhr.responseText); }
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.statusText} ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed: network error"));
    xhr.send(formData);
  });
}

export function getAttachmentDownloadUrl(workItemId, attachmentId) {
  return `${BASE_URL}/work-items/${workItemId}/attachments/${attachmentId}`;
}

// Operator certification actions (slice 2). Each posts to a dedicated
// endpoint and returns the updated work item.
export function certifyWorkItem(id, certificationNote) {
  const body = certificationNote != null && certificationNote !== ""
    ? { certification_note: certificationNote }
    : {};
  return postJson(`/work-items/${id}/certify`, body);
}

export function rejectWorkItem(id, rejectionReason) {
  return postJson(`/work-items/${id}/reject`, {
    rejection_reason: rejectionReason,
  });
}

export function rejectWorkItemWithChanges(id, changeRequest) {
  return postJson(`/work-items/${id}/reject-with-changes`, {
    change_request: changeRequest,
  });
}

// Repo safety / lock APIs (slice 3). The gate lives in the backend builder
// flow; these helpers let the UI read state and clear stale locks.
export function checkRepoSafety(repoPath) {
  const qs = new URLSearchParams({ repo_path: repoPath }).toString();
  return getJson(`/repo-safety/check?${qs}`);
}

export function getActiveRepoLocks() {
  return getJson(`/repo-locks`);
}

export function releaseRepoLock(lockId) {
  return deleteRequest(`/repo-locks/${lockId}`);
}
