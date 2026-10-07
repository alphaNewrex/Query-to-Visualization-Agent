export class ApiError extends Error {
  /** A backend code (`invalid_request`, `planner_unavailable`, ...), the proxy's `backend_unreachable`, or `network_error`, `http_error`, `invalid_response`. */
  readonly code: string;
  /** The HTTP status; 0 when no answer arrived. */
  readonly status: number;
  readonly requestId: string | null;
  readonly isRetryable: boolean;
  readonly details: Record<string, unknown>;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    requestId?: string | null;
    isRetryable?: boolean;
    details?: Record<string, unknown>;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId ?? null;
    this.isRetryable = init.isRetryable ?? false;
    this.details = init.details ?? {};
  }
}
