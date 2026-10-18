import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Bundled with the repository (app/fonts, SIL OFL): no font service is contacted at build or run time.
const sans = localFont({
  variable: "--font-sans-local",
  display: "swap",
  src: [
    { path: "./fonts/NotoSans-Regular.ttf", weight: "400", style: "normal" },
    { path: "./fonts/NotoSans-Bold.ttf", weight: "700", style: "normal" },
  ],
});

// Inter (SIL OFL, variable weight, Latin) is the face people see; Noto Sans stays as the fallback for letters Inter's Latin file lacks.
const inter = localFont({ variable: "--font-inter", display: "swap", src: "./fonts/Inter-latin-wght.woff2", weight: "100 900" });

export const metadata: Metadata = {
  title: "business-platform",
  description: "Multi-tenant, modular business management platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${sans.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
