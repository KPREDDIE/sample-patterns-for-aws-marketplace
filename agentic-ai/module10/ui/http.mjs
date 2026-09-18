/** Explain known failures without displaying untrusted backend error text. */
export async function responseMessage(response) {
  if (response.status === 429) {
    let body = {};
    try { body = await response.json(); } catch { /* The proxy may not return JSON. */ }
    if (body.source === "team-concurrency")
      return "Another investigation is running for your team. Wait for it to finish, then try again.";
    if (body.type === "QUOTA_EXCEEDED" || body.message === "Limit Exceeded")
      return "Your team has reached its shared API quota. Browser and terminal activity use the same allowance. Ask the demo owner to restore quota.";
    if (body.type === "THROTTLED")
      return "Too many requests at once. Try again.";
    return "The service is limiting requests. Try again; if this continues, ask the demo owner to check the team quota.";
  }
  return ({ 400: "Check your request and try again.", 403: "This account is not permitted to perform this action.",
    404: "This request is unavailable to your account.", 409: "This request ID is already in use. Check its status.",
    413: "The request is too long.",
  })[response.status] || `The service could not complete the request (HTTP ${response.status}).`;
}
