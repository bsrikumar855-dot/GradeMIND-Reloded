import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "GradeMIND", template: "%s · GradeMIND" },
  description: "Examiner-assisted grading of handwritten answer scripts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:ring-2 focus:ring-ring"
        >
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
