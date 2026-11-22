/** Orchestrator view — thin wrapper over the transcript module (15.D5b). */

import { createTranscript } from "./transcript.js";

export function createOrchestrator({ auth, post, selectTask, onPlanApproved, onPlanRejected, onToast, clock }) {
  const transcript = createTranscript({
    auth,
    post,
    selectTask,
    onPlanApproved,
    onPlanRejected,
    onToast,
  });

  return {
    renderThread: transcript.renderThread,
    bind: transcript.bind,
    syncModels: transcript.syncModels,
    resetPlanChoice: transcript.resetPlanChoice,
    clock,
    auth,
    getMode: transcript.getMode,
    getTickMs: transcript.getTickMs,
    getDomCount: transcript.getDomCount,
    _test: transcript._test,
    setMode: transcript._test.setMode,
  };
}
