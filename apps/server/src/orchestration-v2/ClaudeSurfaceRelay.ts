import { type ClaudeSurfaceEvent, ClaudeSurfaceError, type ThreadId } from "@t3tools/contracts";
import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as PubSub from "effect/PubSub";
import * as Stream from "effect/Stream";

/** The part of a live Claude query a remote surface drives. */
export interface ClaudeSurfaceSession {
  readonly request: (request: Record<string, unknown>) => Promise<unknown>;
}

export interface ClaudeSurfaceRelayService {
  /** Registers the thread's live session; the returned effect unregisters it. */
  readonly open: (
    threadId: ThreadId,
    session: ClaudeSurfaceSession,
  ) => Effect.Effect<Effect.Effect<void>>;
  /** Forwards one `ui_*` system push from the thread's session. */
  readonly publish: (threadId: ThreadId, event: ClaudeSurfaceEvent) => Effect.Effect<void>;
  readonly request: (
    threadId: ThreadId,
    request: Record<string, unknown>,
  ) => Effect.Effect<unknown, ClaudeSurfaceError>;
  readonly subscribe: (threadId: ThreadId) => Stream.Stream<ClaudeSurfaceEvent>;
}

/** Whether an SDK message is one of the CLI's `ui_*` system pushes. */
export const isClaudeSurfacePush = (message: unknown): message is ClaudeSurfaceEvent => {
  const value = message as { readonly type?: unknown; readonly subtype?: unknown } | null;
  return (
    value !== null &&
    typeof value === "object" &&
    value.type === "system" &&
    typeof value.subtype === "string" &&
    value.subtype.startsWith("ui_")
  );
};

/**
 * Relays Claude Code's remote-surface UI protocol between a thread's live
 * Claude session and the clients drawing its mods. The default reference
 * relays nothing, keeping adapter construction dependency-free in tests; the
 * live layer must be the same reference the adapter infrastructure and the
 * RPC layer get, so layer memoization yields one registry.
 */
export class ClaudeSurfaceRelay extends Context.Reference<ClaudeSurfaceRelayService>(
  "t3/orchestration-v2/ClaudeSurfaceRelay",
  {
    defaultValue: () => ({
      open: () => Effect.succeed(Effect.void),
      publish: () => Effect.void,
      request: () => Effect.fail(new ClaudeSurfaceError({ reason: "no_session" })),
      subscribe: () => Stream.empty,
    }),
  },
) {}

export const layer = Layer.effect(
  ClaudeSurfaceRelay,
  Effect.gen(function* () {
    const sessions = new Map<ThreadId, ClaudeSurfaceSession>();
    const events = yield* PubSub.unbounded<{
      readonly threadId: ThreadId;
      readonly event: ClaudeSurfaceEvent;
    }>();
    const publish = (threadId: ThreadId, event: ClaudeSurfaceEvent) =>
      PubSub.publish(events, { threadId, event }).pipe(Effect.asVoid);

    return {
      open: (threadId, session) =>
        Effect.sync(() => sessions.set(threadId, session)).pipe(
          // A new CLI process knows no surface: clients attach again and redraw.
          Effect.andThen(publish(threadId, { type: "system", subtype: "session_opened" })),
          Effect.as(
            Effect.sync(() => {
              if (sessions.get(threadId) === session) sessions.delete(threadId);
            }),
          ),
        ),
      publish,
      request: (threadId, request) => {
        const subtype = request["subtype"];
        if (typeof subtype !== "string" || !subtype.startsWith("ui_")) {
          return Effect.fail(new ClaudeSurfaceError({ reason: "unsupported_request" }));
        }
        const session = sessions.get(threadId);
        if (session === undefined) {
          return Effect.fail(new ClaudeSurfaceError({ reason: "no_session" }));
        }
        return Effect.tryPromise({
          try: () => session.request(request),
          catch: (cause) =>
            new ClaudeSurfaceError({
              reason: "request_failed",
              detail: (cause instanceof Error ? cause.message : String(cause)).slice(0, 500),
            }),
        });
      },
      subscribe: (threadId) =>
        Stream.fromPubSub(events).pipe(
          Stream.filter((entry) => entry.threadId === threadId),
          Stream.map((entry) => entry.event),
        ),
    } satisfies ClaudeSurfaceRelayService;
  }),
);
