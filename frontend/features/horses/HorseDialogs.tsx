"use client";

import { useState } from "react";

import { ServiceList } from "@/components/history/ServiceList";
import { Button, ButtonIcon, ICONS } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { HorseDocuments } from "@/features/horses/HorseDocuments";
import { HorseNotes } from "@/features/horses/HorseNotes";
import { HorseJournalSection } from "@/features/journal/HorseJournalSection";
import type { HorseDocument, HorseNote, JournalSummary, ServiceRecord } from "@/lib/api/types";

type Window = "journal" | "documents" | "notes" | "services";

/**
 * What belongs to a horse besides its own fields, as tinted buttons (beside "Deactivate horse") that open pop-up windows, the way an invoice
 * and a catalog item do: the journal, the documents, the notes and the services performed, each with how many there are. The windows hold
 * the very same sections as before. Whoever has no journal (an accountant, a viewer) gets no Journal button.
 */
export function HorseDialogs({
  orgId,
  horseId,
  journal,
  journalHasMore,
  documents,
  notes,
  services,
  canWrite,
  timeZone,
}: {
  orgId: string;
  horseId: string;
  /** The latest entries; null when the person has no journal. */
  journal: JournalSummary[] | null;
  journalHasMore: boolean;
  documents: HorseDocument[];
  notes: HorseNote[];
  services: ServiceRecord[];
  canWrite: boolean;
  timeZone: string | null;
}) {
  const [open, setOpen] = useState<Window | null>(null);
  const close = () => setOpen(null);
  return (
    <>
      {journal !== null && (
        <Button type="button" variant="tonal" onClick={() => setOpen("journal")} data-testid="open-journal">
          <ButtonIcon path={ICONS.journal} />
          {`Journal (${journal.length}${journalHasMore ? "+" : ""})`}
        </Button>
      )}
      <Button type="button" variant="tonal" onClick={() => setOpen("documents")} data-testid="open-documents">
        <ButtonIcon path={ICONS.document} />
        {`Documents (${documents.length})`}
      </Button>
      <Button type="button" variant="tonal" onClick={() => setOpen("notes")} data-testid="open-notes">
        <ButtonIcon path={ICONS.note} />
        {`Notes (${notes.length})`}
      </Button>
      <Button type="button" variant="tonal" onClick={() => setOpen("services")} data-testid="open-services">
        <ButtonIcon path={ICONS.service} />
        {`Services performed (${services.length})`}
      </Button>
      {journal !== null && (
        <Modal open={open === "journal"} onClose={close} title="Journal of visits and treatments" testId="journal-dialog" wide>
          <HorseJournalSection orgId={orgId} horseId={horseId} entries={journal} hasMore={journalHasMore} timeZone={timeZone} bare />
        </Modal>
      )}
      <Modal open={open === "documents"} onClose={close} title="Documents" testId="documents-dialog">
        <HorseDocuments horseId={horseId} documents={documents} canWrite={canWrite} timeZone={timeZone} bare />
      </Modal>
      <Modal open={open === "notes"} onClose={close} title="Notes" testId="notes-dialog">
        <HorseNotes horseId={horseId} notes={notes} canWrite={canWrite} timeZone={timeZone} bare />
      </Modal>
      <Modal open={open === "services"} onClose={close} title="Services performed on this horse" testId="services-dialog" wide>
        <ServiceList orgId={orgId} services={services} timeZone={timeZone} title="Services performed on this horse" bare />
      </Modal>
    </>
  );
}
