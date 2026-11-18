// Start an ingest job and watch it with backoff-aware polling.
// Polling pauses while the tab is hidden and resumes at once when it becomes visible.

const TERMINAL = new Set(["succeeded", "failed"]);

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/** POST to a prefkit API address. Resolves to the JSON body; rejects with a readable message. */
async function postJson(url) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRFToken": csrfToken(), Accept: "application/json" },
  });
  // Error replies are JSON too, but fall back to an empty object if the body is not JSON.
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error ?? `HTTP ${response.status}`);
  }
  return body;
}

/** Start an ingest. Resolves to { job_id, status_url }; a 409 rejects with the server's message. */
export function startIngest(url) {
  return postJson(url);
}

/** Ask a running import to stop. Resolves once the server has recorded the request. */
export function cancelJob(url) {
  return postJson(url);
}

/**
 * Poll a job's status URL until it reaches a terminal state.
 * Returns a function that stops watching.
 */
export function watchJob(statusUrl, { onUpdate, onError, minDelay = 1000, maxDelay = 10000 }) {
  const controller = new AbortController();
  let delay = minDelay;
  let lastKey = null;
  let timer = null;

  const schedule = (ms) => {
    clearTimeout(timer);
    timer = setTimeout(tick, ms);
  };

  async function tick() {
    if (document.hidden) {
      schedule(maxDelay); // stay quiet in background tabs; visibilitychange resumes us
      return;
    }
    try {
      const res = await fetch(statusUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      onUpdate(data);
      if (TERMINAL.has(data.status)) return;

      // Back off while nothing changes; reset as soon as progress moves.
      const key = `${data.live?.accepted ?? ""}:${data.live?.quarantined ?? ""}:${data.status}`;
      delay = key === lastKey ? Math.min(delay * 1.5, maxDelay) : minDelay;
      lastKey = key;
      schedule(delay);
    } catch (err) {
      if (err.name === "AbortError") return;
      onError?.(err);
      delay = Math.min(delay * 2, maxDelay);
      schedule(delay);
    }
  }

  const onVisible = () => {
    if (!document.hidden) schedule(0);
  };
  document.addEventListener("visibilitychange", onVisible);
  schedule(0);

  return () => {
    controller.abort();
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
