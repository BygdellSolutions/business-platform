import type { Metadata } from "next";

import { PRIMARY_LINK, SECONDARY_LINK } from "@/components/ui/Button";
import { getCredential, loginPath } from "@/lib/auth/credential";

// Evaluated per request: the sign-in target depends on the runtime auth mode (see the login page).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Business Platform: bookings, orders and invoices in one place",
  description: "Keep customers, appointments, orders, stock and invoices together. Made first for a horse therapy practice, built to fit any small business.",
};

const STEPS = [
  { title: "Book", text: "Put the appointment in the calendar with the customer, the services and, if it applies, the horses. Drag it to another time when plans change." },
  { title: "Order", text: "One click turns the booking into a draft order, priced from your catalog and your discounts. Change the booking later and update the order to match." },
  { title: "Invoice", text: "Turn finished orders into invoices that carry what Swedish invoices need, and download them as PDF. Mistakes are fixed with a credit note, never by editing history." },
] as const;

const AREAS = [
  { title: "Customers and suppliers", text: "People and companies in one list, with their orders, invoices and (for horse owners and stables) their horses on the same page." },
  { title: "Catalog and stock", text: "Products and services with unit, price and VAT. Stock levels follow every delivery and sale, so you can see what is left." },
  { title: "Scheduling", text: "Week, day and month views, a column for each person, and bookings for several horses in one session." },
  { title: "Horse journal", text: "A record for every horse with its owner and stable, and a treatment journal with marks on a body diagram. Drafts save themselves; you finalize when the visit is done." },
  { title: "Invoices and credit notes", text: "Sequential numbers, payment reference, VAT per line and PDF copies. Invoices in another currency also show the VAT in SEK." },
  { title: "Your team", text: "Invite colleagues and give each one a role: owner, admin, accountant, employee or viewer. People only see and do what their role allows." },
] as const;

const SAFEGUARDS = [
  { title: "Every organization is walled off", text: "Your customers, prices and invoices belong to your organization alone. Nobody else can read them, and the system treats another organization's records as if they did not exist." },
  { title: "Roles are enforced on the server", text: "Hiding a button is not security. Every action is checked against the person's role before anything is saved." },
  { title: "Your data can leave with you", text: "Download a complete copy of your organization's data at any time. Deleting an organization asks for a recent copy first." },
] as const;

/** Public landing page. Signed-in visitors never reach it: proxy.ts sends them to /organizations. */
export default async function Home() {
  // Signed-in visitors get here only through a "Go to homepage" link (proxy.ts skips its redirect for /?home).
  const signedIn = (await getCredential()) !== null;
  const signIn = signedIn ? "/organizations" : loginPath();
  const action = signedIn ? "Open the app" : "Sign in";
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border bg-topbar">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <span className="text-base font-semibold tracking-tight">Business Platform</span>
          <nav aria-label="Page sections" className="hidden items-center gap-6 text-sm text-secondary sm:flex">
            <a href="#how" className="hover:text-foreground">How it works</a>
            <a href="#features" className="hover:text-foreground">What it does</a>
            <a href="#safe" className="hover:text-foreground">Your data</a>
          </nav>
          <a href={signIn} className={SECONDARY_LINK}>
            {action}
          </a>
        </div>
      </header>

      <main className="flex-1">
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 py-16 sm:px-6 lg:grid-cols-[1.1fr_0.9fr] lg:py-24">
          <div className="flex flex-col gap-6">
            <h1 className="max-w-xl text-4xl leading-[1.1] font-semibold tracking-tight sm:text-5xl">
              Bookings, orders and invoices, without the spreadsheet.
            </h1>
            <p className="max-w-lg text-lg leading-relaxed text-secondary">
              Keep your customers, appointments, price list, stock and invoices in one place. Built first for a horse therapy practice, and shaped to fit any small business that sells time and products.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <a href={signIn} className={`${PRIMARY_LINK} h-11 px-5 text-base`}>
                {action}
              </a>
              <a href="#how" className={`${SECONDARY_LINK} h-11 px-5 text-base`}>
                See how it works
              </a>
            </div>
            {!signedIn && <p className="text-sm text-muted">Accounts are by invitation. Ask the person who runs your organization.</p>}
          </div>

          {/* A drawn example of one job moving through the system; decorative, so hidden from screen readers. */}
          <div aria-hidden="true" className="flex flex-col gap-3">
            <div className="rounded-xl border border-border bg-panel p-4 shadow-sm">
              <p className="text-xs text-muted">Booking · Tuesday 10:00 to 11:00</p>
              <p className="mt-1 font-medium">Horse massage</p>
              <p className="text-sm text-secondary">Kalle · Anna Andersson · Umeå HK</p>
            </div>
            <div className="ml-6 h-5 w-px bg-border-strong" />
            <div className="rounded-xl border border-border bg-panel p-4 shadow-sm">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted">Draft order</p>
                <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium">Draft</span>
              </div>
              <div className="mt-2 flex justify-between text-sm">
                <span>1 × Horse massage</span>
                <span>850,00 SEK</span>
              </div>
            </div>
            <div className="ml-6 h-5 w-px bg-border-strong" />
            <div className="rounded-xl border border-accent bg-panel p-4 shadow-md">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted">Invoice</p>
                <span className="rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-white">Issued</span>
              </div>
              <div className="mt-2 flex justify-between text-sm text-secondary">
                <span>Amount</span>
                <span>850,00 SEK</span>
              </div>
              <div className="flex justify-between text-sm text-secondary">
                <span>VAT 25 %</span>
                <span>212,50 SEK</span>
              </div>
              <div className="mt-2 flex justify-between border-t border-border pt-2 font-semibold">
                <span>To pay</span>
                <span>1 062,50 SEK</span>
              </div>
            </div>
            <p className="text-center text-xs text-muted">Example</p>
          </div>
        </section>

        <section id="how" className="scroll-mt-4 border-y border-border bg-panel">
          <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
            <h2 className="max-w-xl text-3xl font-semibold tracking-tight">One job, three steps, nothing typed twice</h2>
            <ol className="mt-10 grid gap-10 md:grid-cols-3">
              {STEPS.map((step, index) => (
                <li key={step.title} className="flex gap-4">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">{index + 1}</span>
                  <div>
                    <h3 className="text-lg font-semibold">{step.title}</h3>
                    <p className="mt-2 leading-relaxed text-secondary">{step.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="features" className="mx-auto max-w-6xl scroll-mt-4 px-4 py-16 sm:px-6">
          <h2 className="max-w-xl text-3xl font-semibold tracking-tight">Everything the business needs, in one place</h2>
          <dl className="mt-10 grid gap-x-12 md:grid-cols-2">
            {AREAS.map((area) => (
              <div key={area.title} className="border-t border-border py-6">
                <dt className="text-lg font-semibold">{area.title}</dt>
                <dd className="mt-2 max-w-md leading-relaxed text-secondary">{area.text}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section id="safe" className="scroll-mt-4 bg-foreground text-background">
          <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
            <h2 className="max-w-xl text-3xl font-semibold tracking-tight">Your business data stays yours</h2>
            <div className="mt-10 grid gap-10 md:grid-cols-3">
              {SAFEGUARDS.map((item) => (
                <div key={item.title}>
                  <h3 className="text-lg font-semibold">{item.title}</h3>
                  <p className="mt-2 leading-relaxed opacity-80">{item.text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mx-auto flex max-w-6xl flex-col items-start gap-4 px-4 py-16 sm:px-6">
          <h2 className="max-w-xl text-3xl font-semibold tracking-tight">Made for a horse practice, ready for any business</h2>
          <p className="max-w-xl leading-relaxed text-secondary">
            Horses, owners, stables and treatment journals are a module on top of a general platform. Customers, prices, orders and invoices do not assume you work with horses, so the same foundation can serve other kinds of businesses.
          </p>
          <a href={signIn} className={`${PRIMARY_LINK} h-11 px-5 text-base`}>
            {action}
          </a>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-6 text-sm text-muted sm:px-6">
          <span>Business Platform</span>
          <a href="mailto:support@bygdell.solutions" className="hover:text-foreground">
            support@bygdell.solutions
          </a>
        </div>
      </footer>
    </div>
  );
}
