import { Notice } from "@/components/ui/Notice";

/** The messages of a failed save that do not belong to one control. */
export function ErrorSummary({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <Notice tone="error" testId="form-error">
      {messages.length === 1 ? messages[0] : (
        <ul className="list-disc pl-5">
          {messages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
    </Notice>
  );
}
