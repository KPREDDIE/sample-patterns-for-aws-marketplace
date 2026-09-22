import { sharedFlagControl } from "./planning.mjs";

export function resilienceControl(api) {
  return sharedFlagControl(api, {
    id: "resilience", label: "Fault injection",
    validate: body => body.backend === "portkey",
    refreshed: "",
    changed: body => `Fault injection ${body.enabled ? "enabled" : "disabled"}.`,
  });
}
