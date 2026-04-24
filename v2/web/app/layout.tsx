// Root layout — wraps the whole app in ClerkProvider so the Clerk hooks
// (useAuth, useUser, <SignedIn/>, <SignedOut/>) work everywhere.
import type { ReactNode } from "react";
import { ClerkProvider } from "@clerk/nextjs";

export const metadata = {
  title: "Claude Trader",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body>{children}</body>
      </html>
    </ClerkProvider>
  );
}
