// Issue the application's access-token scope. Membership remains server-managed.
export async function handler(event) {
  event.response = {
    claimsAndScopeOverrideDetails: {
      accessTokenGeneration: { scopesToAdd: ["module10/invoke"] },
    },
  };
  return event;
}
