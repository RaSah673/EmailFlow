export type User = { username: string; role: 'admin' | 'operator' | 'viewer'; csrf: string };

let csrf = '';
export function setCsrf(value: string) {
  csrf = value;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch('/api' + path, { ...options, headers, credentials: 'same-origin' });
  if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me' && path !== '/auth/register/verify') {
    window.dispatchEvent(new Event('session-expired'));
  }
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw new Error(typeof failure.detail === 'string' ? failure.detail : response.statusText || 'Request failed');
  }
  return response.json();
}
