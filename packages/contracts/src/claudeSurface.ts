import * as Schema from "effect/Schema";

import { ThreadId } from "./baseSchemas.ts";

/**
 * Claude Code mods draw panes, the band above the prompt, toasts and status
 * lines. A frontend draws them as a remote surface: it sends the CLI's `ui_*`
 * control requests through the thread's live Claude session and receives the
 * CLI's `ui_*` system pushes. The server relays both unchanged; the CLI
 * validates every request and tree.
 */
export const ClaudeSurfaceRequestInput = Schema.Struct({
  threadId: ThreadId,
  /** A `ui_*` control request body, `subtype` included. */
  request: Schema.Record(Schema.String, Schema.Unknown),
});
export type ClaudeSurfaceRequestInput = typeof ClaudeSurfaceRequestInput.Type;

export const ClaudeSurfaceSubscribeInput = Schema.Struct({ threadId: ThreadId });
export type ClaudeSurfaceSubscribeInput = typeof ClaudeSurfaceSubscribeInput.Type;

/**
 * A `ui_*` system push from the thread's Claude session, or `session_opened`
 * when a new session starts: the client attaches again and re-asks what it
 * draws.
 */
export const ClaudeSurfaceEvent = Schema.Record(Schema.String, Schema.Unknown);
export type ClaudeSurfaceEvent = typeof ClaudeSurfaceEvent.Type;

export const ClaudeSurfaceFailureReason = Schema.Literals([
  "no_session",
  "unsupported_request",
  "request_failed",
]);
export type ClaudeSurfaceFailureReason = typeof ClaudeSurfaceFailureReason.Type;

const CLAUDE_SURFACE_FAILURE_MESSAGES = {
  no_session: "The thread has no live Claude session.",
  unsupported_request: "Only ui_* requests can be relayed to Claude.",
  request_failed: "Claude did not complete the UI request.",
} satisfies Record<ClaudeSurfaceFailureReason, string>;

export class ClaudeSurfaceError extends Schema.TaggedError<ClaudeSurfaceError>()(
  "ClaudeSurfaceError",
  {
    reason: ClaudeSurfaceFailureReason,
    detail: Schema.optionalKey(Schema.String),
  },
) {
  override get message(): string {
    const base = CLAUDE_SURFACE_FAILURE_MESSAGES[this.reason];
    return this.detail === undefined ? base : `${base} ${this.detail}`;
  }
}
