// Access tokens live only in memory; sessionStorage holds the single-use PKCE transaction.
const key = "companion.oauth";
const base64url = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const random = () => base64url(crypto.getRandomValues(new Uint8Array(32)));
function claims(token) {
  const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(part));
}

export async function signIn(config) {
  const verifier = random(), state = random(), nonce = random();
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  sessionStorage.setItem(key, JSON.stringify({ verifier, state, nonce, created: Date.now() }));
  const url = new URL("/oauth2/authorize", config.authUrl);
  url.search = new URLSearchParams({ response_type: "code", client_id: config.clientId,
    redirect_uri: config.redirectUri, scope: "openid module10/invoke", state, nonce,
    code_challenge: challenge, code_challenge_method: "S256" });
  location.assign(url);
}

export async function finishSignIn(config) {
  const params = new URLSearchParams(location.search);
  if (!params.has("code") && !params.has("error")) return null;
  const saved = JSON.parse(sessionStorage.getItem(key) || "null");
  sessionStorage.removeItem(key);
  history.replaceState(null, "", location.pathname);
  if (!saved || params.get("state") !== saved.state || Date.now() - saved.created > 600000) {
    throw new Error("Sign-in expired or could not be verified. Please sign in again.");
  }
  if (params.has("error")) throw new Error("Sign-in was not completed. Please try again.");
  const response = await fetch(new URL("/oauth2/token", config.authUrl), {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", client_id: config.clientId,
      redirect_uri: config.redirectUri, code: params.get("code"), code_verifier: saved.verifier }),
  });
  if (!response.ok) throw new Error("Sign-in could not be completed. Please sign in again.");
  const tokens = await response.json();
  // The token endpoint is HTTPS and PKCE bound. ID claims are used only to check
  // the OIDC nonce, never to grant application access. /me verifies the access token.
  const id = claims(tokens.id_token);
  if (id.nonce !== saved.nonce || id.aud !== config.clientId || id.exp * 1000 <= Date.now()) {
    throw new Error("Sign-in response could not be verified.");
  }
  return { accessToken: tokens.access_token, expires: Date.now() + tokens.expires_in * 1000 };
}

export function signOut(config) {
  sessionStorage.removeItem(key);
  sessionStorage.removeItem("companion.pending");
  const url = new URL("/logout", config.authUrl);
  url.search = new URLSearchParams({ client_id: config.clientId, logout_uri: config.redirectUri });
  location.assign(url);
}
