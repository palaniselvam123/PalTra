/**
 * The desk's Google sign-in lasts 12 hours. After that every API call is
 * refused with 401 "Sign in required", and the screens would otherwise just
 * go stale (or say the engine can't be reached). Send the browser to the
 * sign-in page once instead. Nothing on the server stops: the bots keep
 * trading whether a browser is signed in or not.
 */
let leaving = false;

export function onSignedOut(status: number): void {
  if (status !== 401 || leaving || typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  leaving = true;
  window.location.href = "/login/";
}
