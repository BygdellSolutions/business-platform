import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreateOrganizationForm } from "@/features/organizations/CreateOrganizationForm";
import { newRequestKey } from "@/features/organizations/request-key";

const ID = "00000000-0000-4000-8000-0000000000c3";

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let originalLocation: Location;

const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  assign = vi.fn();
  originalLocation = window.location;
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign } });
  document.cookie = "bp_csrf=" + "C".repeat(43);
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
  document.cookie = "bp_csrf=; max-age=0";
});

async function fill(name: string, currency: string) {
  if (name) await userEvent.type(screen.getByLabelText(/Organization name/), name);
  if (currency) await userEvent.type(screen.getByLabelText(/Currency/), currency);
}

const submit = () => userEvent.click(screen.getByTestId("submit"));
const sent = (index = 0) => {
  const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit & { headers: Record<string, string> }];
  return { url, init, body: JSON.parse(String(init.body)), key: init.headers["idempotency-key"] };
};

describe("newRequestKey", () => {
  it("is 43 URL-safe characters, and different every time", () => {
    const keys = new Set(Array.from({ length: 50 }, newRequestKey));
    expect(keys.size).toBe(50);
    for (const key of keys) expect(key).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});

describe("CreateOrganizationForm", () => {
  it("starts empty: no currency is preselected or assumed", () => {
    render(<CreateOrganizationForm />);
    expect(screen.getByLabelText(/Currency/)).toHaveValue("");
    expect(screen.getByLabelText(/Organization name/)).toHaveValue("");
  });

  it("requires a name and an explicit currency, and sends nothing until both are given", async () => {
    render(<CreateOrganizationForm />);

    await submit();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Enter a name for the organization.")).toBeInTheDocument();
    expect(screen.getByText("Choose the currency the organization works in.")).toBeInTheDocument();

    await fill("Fresh Org", "");
    await submit();
    expect(fetchMock).not.toHaveBeenCalled(); // a name alone is not enough: there is no default currency
  });

  it("sends exactly the name and the currency (no owner, role or id) with a retry key and the CSRF echo, then navigates to the new organization", async () => {
    fetchMock.mockImplementation(() => reply(201, { id: ID }));
    render(<CreateOrganizationForm />);

    await fill("  Fresh Org  ", " eur ");
    await submit();

    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/o/${ID}`));
    const { url, init, body, key } = sent();
    expect(url).toBe("/api/organizations");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ name: "Fresh Org", default_currency: "eur" }); // trimmed only; the backend normalizes the code
    expect(key).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(init.headers["x-csrf-token"]).toBe("C".repeat(43));
    expect(Object.keys(init.headers).sort()).toEqual(["accept", "content-type", "idempotency-key", "x-csrf-token"]);
  });

  it("stores nothing about the new organization (no storage, no cookie written)", async () => {
    fetchMock.mockImplementation(() => reply(201, { id: ID }));
    const before = document.cookie;
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EUR");
    await submit();
    await waitFor(() => expect(assign).toHaveBeenCalled());

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe(before);
  });

  it("shows the backend's field error and keeps the form editable after a definite refusal, with a new key for the next attempt", async () => {
    fetchMock.mockImplementationOnce(() => reply(422, { detail: [{ loc: ["body", "default_currency"], msg: "String should match pattern", type: "string_pattern_mismatch" }] }));
    fetchMock.mockImplementation(() => reply(201, { id: ID }));
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EURO");
    await submit();

    expect(await screen.findByText("String should match pattern")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();

    await userEvent.clear(screen.getByLabelText(/Currency/));
    await userEvent.type(screen.getByLabelText(/Currency/), "EUR");
    await submit();
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(sent(1).key).not.toBe(sent(0).key);
  });

  it("says plainly that the account may not create organizations when the backend refuses with 403", async () => {
    fetchMock.mockImplementation(() => reply(403, { detail: { code: "organization_creation_not_allowed", message: "x" } }));
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EUR");
    await submit();

    expect(await screen.findByTestId("creation-refused")).toHaveTextContent("not allowed to create organizations");
    expect(assign).not.toHaveBeenCalled();
  });

  it("after an unknown outcome the same details are retried with the SAME key, and success navigates", async () => {
    fetchMock.mockImplementationOnce(() => Promise.reject(new TypeError("network down")));
    fetchMock.mockImplementation(() => reply(200, { id: ID })); // the backend recognizes the key and returns the first organization
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EUR");
    await submit();

    expect(await screen.findByTestId("creation-unknown")).toHaveTextContent("will not be created twice");
    expect(assign).not.toHaveBeenCalled();

    await submit();
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/o/${ID}`));
    expect(sent(1).key).toBe(sent(0).key);
    expect(sent(1).body).toEqual(sent(0).body);
  });

  it("treats a server error as an unknown outcome too, and keeps the key", async () => {
    fetchMock.mockImplementationOnce(() => reply(502, { detail: "Backend unavailable" }));
    fetchMock.mockImplementation(() => reply(201, { id: ID }));
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EUR");
    await submit();
    expect(await screen.findByTestId("creation-unknown")).toBeInTheDocument();

    await submit();
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(sent(1).key).toBe(sent(0).key);
  });

  it("uses a NEW key when the details changed after an unknown outcome (a different request)", async () => {
    fetchMock.mockImplementationOnce(() => Promise.reject(new TypeError("network down")));
    fetchMock.mockImplementation(() => reply(201, { id: ID }));
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EUR");
    await submit();
    await screen.findByTestId("creation-unknown");

    await userEvent.type(screen.getByLabelText(/Organization name/), " Two");
    await submit();
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(sent(1).key).not.toBe(sent(0).key);
  });

  it("cannot send twice at once (a double click is one request)", async () => {
    let resolve: (response: Response) => void = () => undefined;
    fetchMock.mockImplementation(() => new Promise<Response>((r) => (resolve = r)));
    render(<CreateOrganizationForm />);
    await fill("Fresh Org", "EUR");

    await userEvent.dblClick(screen.getByTestId("submit"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolve(new Response(JSON.stringify({ id: ID }), { status: 201, headers: { "content-type": "application/json" } }));
    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
  });
});
