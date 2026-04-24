// Shared layout for every authenticated screen. Renders the sidebar + the
// user's Clerk avatar/menu. The `(app)` route group keeps this separate
// from the `(auth)` sign-in/up screens which don't want chrome.
import type { ReactNode } from "react";
import { UserButton } from "@clerk/nextjs";

// TODO: port v1 AppSidebar once we settle nav shape.

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="w-60 border-r p-4">
        {/* TODO: sidebar nav (Overview / Trades / Settings / Account) */}
      </aside>
      <div className="flex-1 flex flex-col">
        <header className="h-14 border-b flex items-center justify-end px-4">
          <UserButton />
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
