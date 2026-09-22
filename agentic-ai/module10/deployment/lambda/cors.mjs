export function corsHeaders() {
  return process.env.UI_ORIGIN ? {
    "Access-Control-Allow-Origin": process.env.UI_ORIGIN,
    "Access-Control-Expose-Headers": "X-Request-Id",
    "Vary": "Origin",
  } : {};
}
