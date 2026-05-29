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
