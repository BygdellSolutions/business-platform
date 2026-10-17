import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { LogoSettings } from "@/features/settings/LogoSettings";
import { normalizeError, type ApiResult } from "@/lib/api/errors";
import type { OrganizationLogo } from "@/lib/api/types";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const A = "00000000-0000-4000-8000-0000000000a1";
const mocked = vi.mocked(apiFetch);
const LOGO: OrganizationLogo = { data_base64: "iVBORw0KGgo=", width: 300, height: 100 };
const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });

function mount(logo: OrganizationLogo | null, canEdit = true) {
  return render(
    <OrgScope orgId={A}>
      <LogoSettings logo={logo} canEdit={canEdit} />
    </OrgScope>,
  );
}

const png = (bytes = 10, type = "image/png") => new File([new Uint8Array(bytes)], "logo.png", { type });

beforeEach(() => {
  mocked.mockReset();
  router.refresh.mockReset();
});

describe("what is shown", () => {
  it("shows the current logo, with replace and remove for the owner", () => {
    mount(LOGO);
    expect(screen.getByTestId("logo-preview")).toHaveAttribute("src", `data:image/png;base64,${LOGO.data_base64}`);
    expect(screen.getByTestId("logo-remove")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replace logo" })).toBeDisabled(); // nothing chosen yet
  });

  it("says there is no logo, and offers no remove button", () => {
    mount(null);
    expect(screen.getByTestId("no-logo")).toBeInTheDocument();
    expect(screen.queryByTestId("logo-remove")).toBeNull();
  });

  it("gives anyone but the owner a read-only view", () => {
    mount(LOGO, false);
    expect(screen.getByTestId("logo-preview")).toBeInTheDocument();
    expect(screen.getByTestId("logo-read-only")).toBeInTheDocument();
    expect(screen.queryByTestId("logo-file")).toBeNull();
    expect(screen.queryByTestId("logo-upload")).toBeNull();
  });
});

describe("uploading", () => {
  it("sends the chosen file as base64 JSON and refreshes the page", async () => {
    mocked.mockResolvedValue(ok({}));
    mount(null);
    await userEvent.upload(screen.getByTestId("logo-file"), png());
    await userEvent.click(screen.getByTestId("logo-upload"));

    await waitFor(() => expect(mocked).toHaveBeenCalledTimes(1));
    const [orgId, path, request] = mocked.mock.calls[0];
    expect([orgId, path, request?.method]).toEqual([A, "/organization/logo", "POST"]);
    expect((request?.body as { data_base64: string }).data_base64).toBe(Buffer.from(new Uint8Array(10)).toString("base64"));
    expect(await screen.findByTestId("logo-saved")).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalled();
  });

  it("refuses a wrong type or a too large file before sending anything", async () => {
    mount(null);
    // applyAccept: false so the browser-side accept filter does not hide the wrong type from the handler.
    await userEvent.setup({ applyAccept: false }).upload(screen.getByTestId("logo-file"), png(10, "image/gif"));
    expect(screen.getByText("The logo must be a PNG or JPEG image.")).toBeInTheDocument();
    await userEvent.upload(screen.getByTestId("logo-file"), png(700_001));
    expect(screen.getByText("The file is larger than 700 KB.")).toBeInTheDocument();
    expect(screen.getByTestId("logo-upload")).toBeDisabled();
    expect(mocked).not.toHaveBeenCalled();
  });

  it("shows the backend's reason when it refuses the image", async () => {
    mocked.mockResolvedValue({ ok: false, error: normalizeError(422, { detail: { code: "invalid_logo", message: "The file is not a readable PNG or JPEG image." } }) });
    mount(null);
    await userEvent.upload(screen.getByTestId("logo-file"), png());
    await userEvent.click(screen.getByTestId("logo-upload"));

    expect(await screen.findByText("The file is not a readable PNG or JPEG image.")).toBeInTheDocument();
    expect(router.refresh).not.toHaveBeenCalled();
  });
});

describe("removing", () => {
  it("deletes the logo and refreshes the page", async () => {
    mocked.mockResolvedValue(ok(undefined, 204));
    mount(LOGO);
    await userEvent.click(screen.getByTestId("logo-remove"));

    await waitFor(() => expect(mocked).toHaveBeenCalledWith(A, "/organization/logo", { method: "DELETE" }));
    expect(await screen.findByTestId("logo-removed")).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalled();
  });
});
