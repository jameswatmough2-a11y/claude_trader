// Account — Clerk handles most of this via <UserProfile/>. Account-level
// trading settings (e.g. future per-tenant Binance keys) would live here too.
import { UserProfile } from "@clerk/nextjs";

export default function AccountPage() {
  return (
    <div className="flex justify-center">
      <UserProfile />
    </div>
  );
}
