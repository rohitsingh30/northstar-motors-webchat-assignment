const DEALERSHIP_PLATFORM_URL = "http://127.0.0.1:4010";

export async function resetDealershipFixture(request) {
  if (process.env.REAL_AI_EXHAUSTIVE !== "1") return;
  const response = await request.post(`${DEALERSHIP_PLATFORM_URL}/admin/api/reset`, {
    headers: { "X-Admin-Key": "northstar-local-admin" },
  });
  if (!response.ok()) {
    throw new Error(`Could not reset the dealership test fixture: HTTP ${response.status()}`);
  }
}
