import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { CheckboxField, DecimalField, SelectField, TextAreaField, TextField } from "@/components/ui/Field";

describe("form controls", () => {
  it("label each control and name it after the API field", () => {
    render(
      <>
        <TextField label="Name" name="name" value="" onChange={() => {}} />
        <TextAreaField label="Description" name="description" value="" onChange={() => {}} />
        <SelectField label="Type" name="type" value="a" onChange={() => {}} options={[{ value: "a", label: "A" }]} />
        <CheckboxField label="Active" name="active" checked onChange={() => {}} />
      </>,
    );

    expect(screen.getByLabelText("Name")).toHaveAttribute("name", "name");
    expect(screen.getByLabelText("Description")).toHaveAttribute("name", "description");
    expect(screen.getByLabelText("Type")).toHaveAttribute("name", "type");
    expect(screen.getByLabelText("Active")).toBeChecked();
  });

  it("tie an error to its control for assistive technology, and only that control", () => {
    render(
      <>
        <TextField label="Name" name="name" value="" onChange={() => {}} error={["Field required"]} />
        <TextField label="Email" name="email" value="" onChange={() => {}} />
      </>,
    );

    const name = screen.getByLabelText("Name");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name.getAttribute("aria-describedby")).toBe(screen.getByTestId("error-name").id);
    expect(screen.getByTestId("error-name")).toHaveTextContent("Field required");
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByTestId("error-email")).toBeNull();
  });

  it("is controlled: what is typed is what the state holds", async () => {
    function Harness() {
      const [value, setValue] = useState("");
      return (
        <>
          <TextField label="Name" name="name" value={value} onChange={setValue} />
          <output>{value}</output>
        </>
      );
    }
    render(<Harness />);

    await userEvent.type(screen.getByLabelText("Name"), "Anna");

    expect(screen.getByRole("status")).toHaveTextContent("Anna");
  });
});

describe("DecimalField", () => {
  it("is a text input, not type=number: the browser must not parse or reformat a decimal", () => {
    render(<DecimalField label="Price" name="price_ex_vat" value="8.20" onChange={() => {}} />);

    const input = screen.getByLabelText("Price");
    expect(input).toHaveAttribute("type", "text");
    expect(input).toHaveAttribute("inputmode", "decimal");
    expect(input).toHaveValue("8.20"); // trailing zero kept
  });

  it.each(["0.10", "4.35", "8.20", "9999999999.99", "0001.50", "1,5"])("passes exactly %s through to the state, character for character", async (typed) => {
    const seen: string[] = [];
    function Harness() {
      const [value, setValue] = useState("");
      return (
        <DecimalField
          label="Price"
          name="price_ex_vat"
          value={value}
          onChange={(next) => {
            seen.push(next);
            setValue(next);
          }}
        />
      );
    }
    render(<Harness />);

    await userEvent.type(screen.getByLabelText("Price"), typed);

    expect(screen.getByLabelText("Price")).toHaveValue(typed);
    expect(seen.at(-1)).toBe(typed);
    expect(seen.every((value) => typeof value === "string")).toBe(true);
  });
});
