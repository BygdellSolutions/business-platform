import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useMemo, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { EntityPicker, type PickerEntity, type PickerSearch } from "@/components/ui/EntityPicker";
import { OrgScope, useOrgId } from "@/components/shell/org-context";
import { networkError, normalizeError, type ApiResult } from "@/lib/api/errors";

const ANNA: PickerEntity = { id: "11111111-1111-4111-8111-111111111111", label: "Anna Andersson", detail: "anna@example.test" };
const UMEA: PickerEntity = { id: "22222222-2222-4222-8222-222222222222", label: "Umeå HK" };
const OLD: PickerEntity = { id: "33333333-3333-4333-8333-333333333333", label: "Old Owner", inactive: true };

const ok = (entities: PickerEntity[]): ApiResult<PickerEntity[]> => ({ ok: true, status: 200, data: entities });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/** A search that answers from a list, matching names the way a backend would. */
function directory(entities: PickerEntity[]) {
  return vi.fn<PickerSearch>(async (query) => ok(entities.filter((entity) => entity.label.toLowerCase().includes(query.toLowerCase()))));
}

function Harness({
  search,
  initial = null,
  clearable = false,
  disabled,
  readOnly,
  onValue,
}: {
  search: PickerSearch;
  initial?: PickerEntity | null;
  clearable?: boolean;
  disabled?: boolean;
  readOnly?: boolean;
  onValue?: (entity: PickerEntity | null) => void;
}) {
  const [value, setValue] = useState<PickerEntity | null>(initial);
  return (
    <EntityPicker
      label="Owner"
      name="owner_customer_id"
      value={value}
      onChange={(entity) => {
        setValue(entity);
        onValue?.(entity);
      }}
      search={search}
      clearable={clearable}
      disabled={disabled}
      readOnly={readOnly}
    />
  );
}

const combobox = () => screen.getByRole("combobox", { name: "Owner" });
const hidden = () => document.querySelector<HTMLInputElement>('input[type="hidden"][name="owner_customer_id"]')!;
const optionNames = () => screen.queryAllByRole("option").map((option) => option.textContent);

describe("showing the selection", () => {
  it("displays the current entity by its label and carries its id, not its text", () => {
    render(<Harness search={directory([])} initial={ANNA} />);

    expect(combobox()).toHaveValue("Anna Andersson");
    expect(hidden().value).toBe(ANNA.id);
    expect(hidden()).toHaveAttribute("type", "hidden");
    expect(combobox()).not.toHaveAttribute("name"); // the visible text is never submitted
  });

  it("shows an inactive selection and says it is inactive", () => {
    render(<Harness search={directory([])} initial={OLD} />);
    expect(combobox()).toHaveValue("Old Owner (inactive)");
    expect(hidden().value).toBe(OLD.id);
  });

  it("shows a placeholder and an empty id when nothing is selected", () => {
    render(<Harness search={directory([])} />);
    expect(combobox()).toHaveValue("");
    expect(combobox()).toHaveAttribute("placeholder", "Type to search");
    expect(hidden().value).toBe("");
  });

  it("keeps an unoffered (inactive) selection when other things happen: opening and closing changes nothing", async () => {
    const search = directory([ANNA, UMEA]); // OLD is not offered, like an inactive customer
    render(<Harness search={search} initial={OLD} />);

    await userEvent.click(combobox());
    await screen.findAllByRole("option");
    await userEvent.keyboard("{Escape}");

    expect(combobox()).toHaveValue("Old Owner (inactive)");
    expect(hidden().value).toBe(OLD.id);
  });
});

describe("loading choices", () => {
  it("searches when opened, shows a loading state, then the choices", async () => {
    const first = deferred<ApiResult<PickerEntity[]>>();
    const search = vi.fn<PickerSearch>().mockReturnValue(first.promise);
    render(<Harness search={search} />);

    await userEvent.click(combobox());

    expect(search).toHaveBeenCalledWith("", expect.any(AbortSignal));
    expect(screen.getByTestId("picker-status")).toHaveTextContent("Searching…");
    expect(combobox()).toHaveAttribute("aria-expanded", "true");

    await act(async () => first.resolve(ok([ANNA, UMEA])));

    expect(optionNames()).toEqual(["Anna Anderssonanna@example.test", "Umeå HK"]);
    expect(screen.queryByTestId("picker-status")).toBeNull();
  });

  it("searches again for every change of the text, with the trimmed text (leading spaces alone are no change)", async () => {
    const search = directory([ANNA, UMEA]);
    render(<Harness search={search} />);

    await userEvent.click(combobox());
    await userEvent.type(combobox(), " um");

    await waitFor(() => expect(optionNames()).toEqual(["Umeå HK"]));
    expect(search.mock.calls.map(([query]) => query)).toEqual(["", "u", "um"]);
  });

  it("says when nothing matches", async () => {
    render(<Harness search={directory([ANNA])} />);

    await userEvent.type(combobox(), "zzz");

    expect(await screen.findByText("No matches")).toBeInTheDocument();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it.each([
    [{ ok: false, error: networkError() } as ApiResult<PickerEntity[]>, "Could not reach the server"],
    [{ ok: false, error: normalizeError(403, { detail: "Your role does not allow this." }) } as ApiResult<PickerEntity[]>, "Your role does not allow this."],
    [{ ok: false, error: normalizeError(500, { detail: "Traceback" }) } as ApiResult<PickerEntity[]>, "could not complete the request"],
  ])("shows an error for %# and can try again", async (failure, text) => {
    const search = vi.fn<PickerSearch>().mockResolvedValueOnce(failure).mockResolvedValueOnce(ok([ANNA]));
    render(<Harness search={search} />);

    await userEvent.click(combobox());
    expect(await screen.findByTestId("picker-error")).toHaveTextContent(text);

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findAllByRole("option")).toHaveLength(1);
    expect(screen.queryByTestId("picker-error")).toBeNull();
  });

  it("starts from nothing every time it opens: choices of an earlier opening are not shown", async () => {
    const second = deferred<ApiResult<PickerEntity[]>>();
    const search = vi.fn<PickerSearch>().mockResolvedValueOnce(ok([ANNA])).mockReturnValueOnce(second.promise);
    render(<Harness search={search} />);

    await userEvent.click(combobox());
    expect(await screen.findAllByRole("option")).toHaveLength(1);
    await userEvent.keyboard("{Escape}{ArrowDown}"); // close, then open again

    expect(screen.queryAllByRole("option")).toHaveLength(0); // no leftover from the first opening
    expect(screen.getByTestId("picker-status")).toHaveTextContent("Searching…");
    await act(async () => second.resolve(ok([])));
  });
});

describe("stale answers", () => {
  it("a slow answer to an older text never replaces the answer to the newer text", async () => {
    const slow = deferred<ApiResult<PickerEntity[]>>();
    const search = vi.fn<PickerSearch>(async (query) => {
      if (query === "a") return slow.promise; // answered late
      return ok(query === "an" ? [ANNA] : []);
    });
    render(<Harness search={search} />);

    await userEvent.type(combobox(), "a");
    await userEvent.type(combobox(), "n");
    await waitFor(() => expect(optionNames()).toEqual(["Anna Anderssonanna@example.test"]));

    await act(async () => slow.resolve(ok([UMEA]))); // the answer for "a" finally arrives

    expect(optionNames()).toEqual(["Anna Anderssonanna@example.test"]);
  });

  it("aborts the request of the text that was replaced", async () => {
    const signals: AbortSignal[] = [];
    const search = vi.fn<PickerSearch>((query, signal) => {
      signals.push(signal);
      return new Promise(() => {});
    });
    render(<Harness search={search} />);

    await userEvent.type(combobox(), "ab");

    expect(signals.map((signal) => signal.aborted)).toEqual([true, true, false]); // "", "a", "ab"
  });

  it("an answer that arrives after the list was closed is ignored", async () => {
    const late = deferred<ApiResult<PickerEntity[]>>();
    const search = vi.fn<PickerSearch>().mockReturnValueOnce(late.promise).mockResolvedValueOnce(ok([UMEA]));
    render(<Harness search={search} />);

    await userEvent.click(combobox());
    await userEvent.keyboard("{Escape}");
    await act(async () => late.resolve(ok([ANNA])));
    await userEvent.keyboard("{ArrowDown}");

    expect(await screen.findAllByRole("option")).toHaveLength(1);
    expect(optionNames()).toEqual(["Umeå HK"]); // not Anna from the closed opening
  });

  it("an answer that arrives after the picker was removed does nothing (and does not warn)", async () => {
    const late = deferred<ApiResult<PickerEntity[]>>();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = render(<Harness search={() => late.promise} />);
    await userEvent.click(combobox());

    unmount();
    await act(async () => late.resolve(ok([ANNA])));

    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});

describe("choosing", () => {
  it("choosing an option selects its entity: the id is what the picker holds", async () => {
    const onValue = vi.fn();
    render(<Harness search={directory([ANNA, UMEA])} onValue={onValue} />);

    await userEvent.click(combobox());
    await userEvent.click(await screen.findByRole("option", { name: /Umeå HK/ }));

    expect(onValue).toHaveBeenCalledWith(UMEA);
    expect(combobox()).toHaveValue("Umeå HK");
    expect(hidden().value).toBe(UMEA.id);
    expect(combobox()).toHaveAttribute("aria-expanded", "false");
  });

  it("typing the exact name of a customer does NOT select it: text is never identity", async () => {
    const onValue = vi.fn();
    render(<Harness search={directory([ANNA, UMEA])} initial={UMEA} onValue={onValue} />);

    await userEvent.click(combobox());
    await userEvent.clear(combobox());
    await userEvent.type(combobox(), "Anna Andersson");
    await screen.findAllByRole("option");
    await userEvent.tab(); // leaves without choosing

    expect(onValue).not.toHaveBeenCalled();
    expect(hidden().value).toBe(UMEA.id);
    expect(combobox()).toHaveValue("Umeå HK"); // the text reverts to what is really selected
  });

  it("two entities with the same label are different selections", async () => {
    const twin: PickerEntity = { id: "44444444-4444-4444-8444-444444444444", label: "Anna Andersson" };
    const onValue = vi.fn();
    render(<Harness search={directory([ANNA, twin])} onValue={onValue} />);

    await userEvent.click(combobox());
    const options = await screen.findAllByRole("option");
    await userEvent.click(options[1]);

    expect(onValue).toHaveBeenCalledWith(twin);
    expect(hidden().value).toBe(twin.id);
  });

  it("marks the selected option", async () => {
    render(<Harness search={directory([ANNA, UMEA])} initial={UMEA} />);
    await userEvent.click(combobox());
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.getAttribute("aria-selected"))).toEqual(["false", "true"]);
  });
});

describe("keyboard", () => {
  it("arrows move through the choices, Enter chooses, and Enter does not submit the surrounding form", async () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    const onValue = vi.fn();
    render(
      <form onSubmit={onSubmit}>
        <Harness search={directory([ANNA, UMEA])} onValue={onValue} />
        <button type="submit">Save</button> {/* a real form has one; Enter in a field presses it */}
      </form>,
    );

    await userEvent.click(combobox());
    await screen.findAllByRole("option");
    await userEvent.keyboard("{ArrowDown}");
    expect(combobox().getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("option")[0].id);
    await userEvent.keyboard("{ArrowDown}{ArrowDown}"); // stops at the last
    expect(combobox().getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("option")[1].id);
    await userEvent.keyboard("{ArrowUp}{ArrowUp}"); // stops at the first
    expect(combobox().getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("option")[0].id);
    await userEvent.keyboard("{ArrowDown}{Enter}");

    expect(onValue).toHaveBeenCalledWith(UMEA);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("Enter with nothing highlighted leaves the form to submit", async () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Harness search={directory([ANNA])} />
        <button type="submit">Apply</button>
      </form>,
    );

    await userEvent.click(combobox());
    await screen.findAllByRole("option");
    await userEvent.keyboard("{Enter}");

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("Escape closes the list and discards the typed text", async () => {
    render(<Harness search={directory([ANNA, UMEA])} initial={UMEA} />);

    await userEvent.click(combobox());
    await userEvent.type(combobox(), "xyz");
    expect(combobox()).toHaveValue("Umeå HKxyz");
    await userEvent.keyboard("{Escape}");

    expect(combobox()).toHaveValue("Umeå HK");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(combobox()).toHaveAttribute("aria-expanded", "false");
  });

  it("ArrowDown opens a closed list", async () => {
    render(<Harness search={directory([ANNA])} />);
    combobox().focus();
    await userEvent.keyboard("{Escape}{ArrowDown}");
    expect(await screen.findAllByRole("option")).toHaveLength(1);
  });

  it("is a labelled combobox with a labelled listbox", async () => {
    render(<Harness search={directory([ANNA])} />);
    await userEvent.click(combobox());

    expect(combobox()).toHaveAttribute("aria-autocomplete", "list");
    expect(combobox().getAttribute("aria-controls")).toBe(screen.getByRole("listbox", { name: "Owner choices" }).id);
  });
});

describe("clearing", () => {
  it("an optional picker can be cleared, and then holds no id", async () => {
    const onValue = vi.fn();
    render(<Harness search={directory([ANNA])} initial={ANNA} clearable onValue={onValue} />);

    await userEvent.click(screen.getByRole("button", { name: "Clear Owner" }));

    expect(onValue).toHaveBeenCalledWith(null);
    expect(combobox()).toHaveValue("");
    expect(hidden().value).toBe("");
    expect(screen.queryByRole("button", { name: "Clear Owner" })).toBeNull();
  });

  it("a required picker offers no Clear", () => {
    render(<Harness search={directory([])} initial={ANNA} />);
    expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
  });

  it("there is nothing to clear when nothing is selected", () => {
    render(<Harness search={directory([])} clearable />);
    expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
  });
});

describe("disabled and read-only", () => {
  it("disabled shows the selection, cannot be opened, cannot be cleared", async () => {
    const search = directory([ANNA]);
    render(<Harness search={search} initial={ANNA} clearable disabled />);

    expect(combobox()).toBeDisabled();
    expect(combobox()).toHaveValue("Anna Andersson");
    expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
    await userEvent.click(combobox());
    expect(search).not.toHaveBeenCalled();
  });

  it("read-only shows the selection, cannot be changed by typing, opening or keys", async () => {
    const search = directory([ANNA, UMEA]);
    const onValue = vi.fn();
    render(<Harness search={search} initial={ANNA} clearable readOnly onValue={onValue} />);

    await userEvent.click(combobox());
    await userEvent.keyboard("xyz{ArrowDown}{Enter}");

    expect(combobox()).toHaveAttribute("aria-readonly", "true");
    expect(combobox()).toHaveValue("Anna Andersson");
    expect(search).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
    expect(onValue).not.toHaveBeenCalled();
  });
});

describe("errors from the form", () => {
  it("ties an error message to the control", () => {
    render(
      <EntityPicker label="Owner" name="owner_customer_id" value={null} onChange={() => {}} search={directory([])} error={["Field required"]} />,
    );
    expect(combobox()).toHaveAttribute("aria-invalid", "true");
    expect(combobox().getAttribute("aria-describedby")).toBe(screen.getByTestId("error-owner_customer_id").id);
  });
});

describe("organization scope", () => {
  function Scoped({ search }: { search: (orgId: string) => PickerSearch }) {
    const orgId = useOrgId();
    const forOrg = useMemo(() => search(orgId), [orgId, search]);
    return <Harness search={forOrg} />;
  }

  it("a picker of one organization is destroyed with its selection, text and results when the organization changes", async () => {
    const A = "00000000-0000-4000-8000-0000000000a1";
    const B = "00000000-0000-4000-8000-0000000000b2";
    const search = (orgId: string): PickerSearch => async () => ok([{ id: `${orgId}-1`, label: `Customer of ${orgId}` }]);
    const { rerender } = render(
      <OrgScope orgId={A}>
        <Scoped search={search} />
      </OrgScope>,
    );
    await userEvent.click(combobox());
    await userEvent.click(await screen.findByRole("option"));
    expect(hidden().value).toBe(`${A}-1`);
    await userEvent.type(combobox(), "typed in A");

    rerender(
      <OrgScope orgId={B}>
        <Scoped search={search} />
      </OrgScope>,
    );

    expect(combobox()).toHaveValue("");
    expect(hidden().value).toBe("");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.body.textContent).not.toContain(A);
  });

  it("a delayed answer for organization A cannot populate the picker shown for organization B", async () => {
    const A = "00000000-0000-4000-8000-0000000000a1";
    const B = "00000000-0000-4000-8000-0000000000b2";
    const slowForA = deferred<ApiResult<PickerEntity[]>>();
    const search = (orgId: string): PickerSearch => (orgId === A ? () => slowForA.promise : async () => ok([{ id: "b-1", label: "Customer of B" }]));
    const { rerender } = render(
      <OrgScope orgId={A}>
        <Scoped search={search} />
      </OrgScope>,
    );
    await userEvent.click(combobox()); // asks A, no answer yet

    rerender(
      <OrgScope orgId={B}>
        <Scoped search={search} />
      </OrgScope>,
    );
    await userEvent.click(combobox());
    expect(await screen.findAllByRole("option")).toHaveLength(1);
    await act(async () => slowForA.resolve(ok([{ id: "a-1", label: "Customer of A" }])));

    expect(optionNames()).toEqual(["Customer of B"]);
    expect(document.body.textContent).not.toContain("Customer of A");
  });
});
