import { z } from "zod";

export const mappingFailureSchema = z.enum([
  "process_start_failed", "process_startup_deadline_exceeded",
  "process_progress_deadline_exceeded", "process_shutdown_deadline_exceeded",
  "child_reported_failure", "child_exited_without_confirmed_stop",
  "child_health_pipe_closed", "invalid_child_health_protocol",
  "child_descendants_outlived_owner", "process_cleanup_failed",
  "mapping_process_failed", "mapping_service_unavailable",
]);
export type MappingFailure = z.infer<typeof mappingFailureSchema>;
