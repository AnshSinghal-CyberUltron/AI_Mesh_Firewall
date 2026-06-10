/** Derive pipeline badge action from a simulator result object. */
export function deriveResultAction(result) {
  if (!result) return "allow";
  if (result.final_action) return result.final_action;
  if (result.action) return result.action;
  const code = String(result.code || result.error?.code || "").toLowerCase();
  const httpStatus = result.httpStatus ?? result.status;
  if (result.error || (typeof httpStatus === "number" && httpStatus >= 400)) {
    if (code.includes("block") || httpStatus === 403) return "block";
    return "error";
  }
  if (result.success === false) return "error";
  return "allow";
}
