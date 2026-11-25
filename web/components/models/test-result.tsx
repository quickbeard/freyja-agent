import { type LlmLastTest, TEST_STEPS } from "@/lib/models";

const WHEN = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** How a test went, in a few words: `Last test passed`, `The test failed at the conversation`. */
export function outcome(test: LlmLastTest, subject = "Last test"): string {
  if (test.ok) {
    return `${subject} passed`;
  }
  const step = test.step ? TEST_STEPS[test.step] : "an unknown step";
  return `${subject} failed at ${step}`;
}

/**
 * One test's result: how it went, when the service said it ended, and for a failure the
 * endpoint's reason. The time is the service's own, only put in the reader's words here.
 */
export function TestResult({
  test,
  subject,
}: {
  test: LlmLastTest;
  subject?: string;
}) {
  return (
    <>
      <p className={test.ok ? undefined : "text-destructive"}>
        {outcome(test, subject)}
        {" · "}
        <time dateTime={test.at} className="text-muted-foreground">
          {WHEN.format(new Date(test.at))}
        </time>
      </p>
      {!test.ok && test.reason && (
        <p className="break-words text-muted-foreground">{test.reason}</p>
      )}
    </>
  );
}
