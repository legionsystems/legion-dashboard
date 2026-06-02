const BASE_URL = "/api";

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Request failed: ${response.status} ${response.statusText} ${text}`
    );
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
