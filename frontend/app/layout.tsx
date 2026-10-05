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

export const metadata: Metadata = {
  title: "business-platform",
  description: "Multi-tenant, modular business management platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${sans.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
