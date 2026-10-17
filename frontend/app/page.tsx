import { PRIMARY_LINK } from "@/components/ui/Button";
import { loginPath } from "@/lib/auth/credential";

// Evaluated per request: the sign-in target depends on the runtime auth mode (see the login page).
export const dynamic = "force-dynamic";

const FEATURES = [
  { title: "Customers", text: "Keep people and companies in one place, with the history of everything you do for them." },
  { title: "Products and services", text: "A catalog with units, prices and VAT, ready to put on orders and invoices." },
  { title: "Orders and invoicing", text: "From booking to order to invoice, with the numbers calculated for you." },
  { title: "Made for your business", text: "Switch on only the modules and fields your organization needs." },
  { title: "Separate by design", text: "Every organization's data is isolated from every other organization's." },
  { title: "Your team, your rules", text: "Invite colleagues and give each of them the role that fits their work." },
] as const;

/** Public landing page. Signed-in visitors never reach it: proxy.ts sends them to /organizations. */
export default function Home() {
  const signIn = loginPath();
  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-16 px-6 py-16">
      <header className="flex items-center justify-between">
        <span className="text-lg font-semibold tracking-tight">Business Platform</span>
        <a href={signIn} className={PRIMARY_LINK}>
          Sign in
        </a>
      </header>

      <section className="flex flex-col gap-4">
        <h1 className="text-4xl leading-tight font-semibold tracking-tight">Run your business from one place</h1>
        <p className="max-w-2xl text-lg text-zinc-500">
          Customers, products, orders and invoices in one simple, secure platform that adapts to the way your organization works.
        </p>
        <div>
          <a href={signIn} className={PRIMARY_LINK}>
            Sign in to get started
          </a>
        </div>
      </section>

      <section aria-labelledby="features" className="flex flex-col gap-6">
        <h2 id="features" className="text-xl font-semibold tracking-tight">
          What you can do
        </h2>
        <ul className="grid gap-6 sm:grid-cols-2">
          {FEATURES.map((feature) => (
            <li key={feature.title} className="flex flex-col gap-1">
              <h3 className="font-medium">{feature.title}</h3>
              <p className="text-sm text-zinc-500">{feature.text}</p>
            </li>
          ))}
        </ul>
      </section>

      <footer className="border-t border-border pt-6 text-sm text-zinc-500">
        Need an account? Ask the person who runs your organization to invite you.
      </footer>
    </main>
  );
}
