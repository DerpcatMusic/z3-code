import { type ClaudeSurfaceError, ThreadId } from "@t3tools/contracts";
import { assert, it } from "@effect/vitest";
import * as Effect from "effect/Effect";
import * as Fiber from "effect/Fiber";
import * as Stream from "effect/Stream";

import * as ClaudeSurfaceRelay from "./ClaudeSurfaceRelay.ts";

const failureReason = <A>(effect: Effect.Effect<A, ClaudeSurfaceError>) =>
  effect.pipe(
    Effect.match({ onFailure: (error) => error.reason, onSuccess: () => "succeeded" as const }),
  );

const thread = ThreadId.make("thread-surface");
const other = ThreadId.make("thread-other");

it.effect("relays ui requests to the thread's live session and its pushes to subscribers", () =>
  Effect.gen(function* () {
    const relay = yield* ClaudeSurfaceRelay.ClaudeSurfaceRelay;
    const sent: Array<Record<string, unknown>> = [];

    const noSession = yield* failureReason(relay.request(thread, { subtype: "ui_panes" }));
    assert.equal(noSession, "no_session");

    const events = yield* relay
      .subscribe(thread)
      .pipe(Stream.take(2), Stream.runCollect, Effect.forkChild({ startImmediately: true }));
    const close = yield* relay.open(thread, {
      request: async (request) => {
        sent.push(request);
        return { panes: [] };
      },
    });

    assert.deepEqual(yield* relay.request(thread, { subtype: "ui_panes" }), { panes: [] });
    assert.deepEqual(sent, [{ subtype: "ui_panes" }]);
    const refused = yield* failureReason(relay.request(thread, { subtype: "interrupt" }));
    assert.equal(refused, "unsupported_request");
    assert.equal(sent.length, 1);

    yield* relay.publish(other, { type: "system", subtype: "ui_toast", text: "elsewhere" });
    yield* relay.publish(thread, { type: "system", subtype: "ui_toast", text: "here" });
    assert.deepEqual(Array.from(yield* Fiber.join(events)), [
      { type: "system", subtype: "session_opened" },
      { type: "system", subtype: "ui_toast", text: "here" },
    ]);

    yield* close;
    const closed = yield* failureReason(relay.request(thread, { subtype: "ui_panes" }));
    assert.equal(closed, "no_session");
  }).pipe(Effect.provide(ClaudeSurfaceRelay.layer)),
);

it.effect("a closed older session does not unregister its replacement", () =>
  Effect.gen(function* () {
    const relay = yield* ClaudeSurfaceRelay.ClaudeSurfaceRelay;
    const closeFirst = yield* relay.open(thread, { request: async () => "first" });
    const closeSecond = yield* relay.open(thread, { request: async () => "second" });
    yield* closeFirst;
    assert.equal(yield* relay.request(thread, { subtype: "ui_panes" }), "second");
    yield* closeSecond;
  }).pipe(Effect.provide(ClaudeSurfaceRelay.layer)),
);

it("recognizes only ui_* system pushes", () => {
  assert.isTrue(ClaudeSurfaceRelay.isClaudeSurfacePush({ type: "system", subtype: "ui_panes" }));
  assert.isFalse(ClaudeSurfaceRelay.isClaudeSurfacePush({ type: "system", subtype: "init" }));
  assert.isFalse(ClaudeSurfaceRelay.isClaudeSurfacePush({ type: "assistant" }));
  assert.isFalse(ClaudeSurfaceRelay.isClaudeSurfacePush(null));
});
