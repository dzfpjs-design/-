// A confirmed validation rejection may be revised only while the original
// step/version is unchanged and the durable receipt shows no committed action.
export function mayReviseCompletion(error, task, request) {
  return [400,422].includes(error?.status) && !!task && !!request &&
    task.version === request.version &&
    task.workflow_runtime?.current_step_id === request.step_id &&
    Array.isArray(task.workflow_runtime.receipts) &&
    !task.workflow_runtime.receipts.some(r => r.request_id === request.request_id);
}
