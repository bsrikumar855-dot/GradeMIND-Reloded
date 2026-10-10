import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { UsersAdmin } from "@/components/admin/users-admin";
import { apiGet, type Me } from "@/lib/api";
import type { UserRow } from "@/lib/types";

export const metadata: Metadata = { title: "Users" };

export default async function UsersPage() {
  const me = await apiGet<Me>("/api/me");
  if (me.role !== "admin") redirect("/"); // the API refuses everyone else too; this just does not show a page that cannot work
  const users = await apiGet<UserRow[]>("/api/users");
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Users</h1>
      <UsersAdmin meId={me.id} initial={users} />
    </div>
  );
}
