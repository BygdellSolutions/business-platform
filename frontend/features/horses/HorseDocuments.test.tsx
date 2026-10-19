import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HorseDocuments, sizeText } from "@/features/horses/HorseDocuments";
import type { HorseDocument } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/shell/org-context", () => ({ useOrgId: () => "00000000-0000-4000-8000-0000000000a1" }));

const doc = (over: Partial<HorseDocument>): HorseDocument => ({ id: "d1", filename: "Vet report.pdf", content_type: "application/pdf", byte_size: 2048, created_at: "2026-10-18T10:00:00Z", created_by_name: "Tina", ...over });

describe("sizeText", () => {
  it("writes bytes, kilobytes and megabytes", () => {
    expect(sizeText(512)).toBe("512 B");
    expect(sizeText(2048)).toBe("2 KB");
    expect(sizeText(9 * 1024 * 1024)).toBe("9.0 MB");
  });
});

describe("HorseDocuments", () => {
  it("shows a picture as a preview and a PDF as a name, both opening the file in a new tab", () => {
    render(<HorseDocuments horseId="h1" canWrite documents={[doc({}), doc({ id: "d2", filename: "X-ray.png", content_type: "image/png" })]} timeZone={null} />);
    expect(screen.getAllByTestId("horse-document")).toHaveLength(2);
    expect(screen.getAllByTestId("document-preview")).toHaveLength(1);
    const link = screen.getByRole("link", { name: /^X-ray.png$/ });
    expect(link).toHaveAttribute("href", "/api/o/00000000-0000-4000-8000-0000000000a1/horses/h1/documents/d2/file");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("offers adding and deleting only to someone who may change records", () => {
    const { rerender } = render(<HorseDocuments horseId="h1" canWrite documents={[doc({})]} timeZone={null} />);
    expect(screen.getByTestId("document-input")).toBeInTheDocument();
    expect(screen.getByTestId("delete-document")).toBeInTheDocument();
    rerender(<HorseDocuments horseId="h1" canWrite={false} documents={[doc({})]} timeZone={null} />);
    expect(screen.queryByTestId("document-input")).toBeNull();
    expect(screen.queryByTestId("delete-document")).toBeNull();
    expect(screen.getByTestId("document-name")).toBeInTheDocument(); // reading is for everyone
  });

  it("says so when there are none", () => {
    render(<HorseDocuments horseId="h1" canWrite={false} documents={[]} timeZone={null} />);
    expect(screen.getByTestId("no-documents")).toHaveTextContent("No documents yet.");
  });
});
