// API Gateway meters a supplied authorizer usage key even when apiKeyRequired
// is false. Omit it for authenticated, stage-throttled status polls only.
export function isMetered(methodArn) {
  return !["GET/planning", "GET/resilience"].includes(String(methodArn).split("/").slice(2).join("/"));
}
