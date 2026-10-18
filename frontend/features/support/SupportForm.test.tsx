import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SupportForm } from "@/features/support/SupportForm";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("SupportForm", () => {
  it("sends what was written and shows the ticket number", async () => {
    const fetchMock = answer(201, { reference: "SUP-K7M2Q9XD" });
    render(<SupportForm signedInAs={null} />);

    await userEvent.type(screen.getByLabelText("Your name (optional)"), "Anna");
    await userEvent.type(screen.getByLabelText("Your email"), "anna@example.test");
    await userEvent.type(screen.getByLabelText("How can we help?"), "I cannot change a price.");
    await userEvent.click(screen.getByTestId("support-send"));

    await waitFor(() => expect(screen.getByTestId("support-reference")).toHaveTextContent("SUP-K7M2Q9XD"));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/support");
    expect(JSON.parse(String(init.body))).toEqual({ name: "Anna", email: "anna@example.test", message: "I cannot change a price.", website: "" });
  });

  it("does not send an empty message, and counts the characters", async () => {
    render(<SupportForm signedInAs={null} />);
    expect(screen.getByTestId("support-send")).toBeDisabled();
    await userEvent.type(screen.getByLabelText("How can we help?"), "abc");
    expect(screen.getByTestId("support-count")).toHaveTextContent("3 / 4000");
    expect(screen.getByTestId("support-send")).toBeEnabled();
  });

  it("asks nothing about the sender when signed in, and says who the answer goes to", async () => {
    const fetchMock = answer(201, { reference: "SUP-AAAAAAAA" });
    render(<SupportForm signedInAs={{ name: "Tina Therapist", email: "tina@example.test" }} />);

    expect(screen.queryByLabelText("Your email")).toBeNull();
    expect(screen.getByTestId("support-signed-in")).toHaveTextContent("Tina Therapist at tina@example.test");
    await userEvent.type(screen.getByLabelText("How can we help?"), "The date is wrong.");
    await userEvent.click(screen.getByTestId("support-send"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body)).email).toBe("tina@example.test");
  });

  it("explains a refused address next to the field and keeps what was typed", async () => {
    answer(422, { detail: [{ loc: ["body", "email"], msg: "Value error, Enter an email address we can answer, like name@example.com.", type: "value_error" }] });
    render(<SupportForm signedInAs={null} />);
    await userEvent.type(screen.getByLabelText("Your email"), "nope");
    await userEvent.type(screen.getByLabelText("How can we help?"), "Please help me with this.");
    await userEvent.click(screen.getByTestId("support-send"));

    await waitFor(() => expect(screen.getByText(/Enter an email address we can answer/)).toBeInTheDocument());
    expect(screen.getByLabelText("How can we help?")).toHaveValue("Please help me with this.");
    expect(screen.queryByTestId("support-sent")).toBeNull();
  });

  it("says plainly when too many messages were sent", async () => {
    answer(429, { detail: { code: "rate_limited", message: "You have sent several messages already. Wait a while and try again." } });
    render(<SupportForm signedInAs={null} />);
    await userEvent.type(screen.getByLabelText("Your email"), "anna@example.test");
    await userEvent.type(screen.getByLabelText("How can we help?"), "One more question for you.");
    await userEvent.click(screen.getByTestId("support-send"));
    await waitFor(() => expect(screen.getByText(/Wait a while and try again/)).toBeInTheDocument());
  });
});
