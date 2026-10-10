"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, ClientError } from "@/lib/client";
import { temporaryPassword } from "@/lib/temp-password";
import type { UserRow } from "@/lib/types";

const ROLE_LABEL: Record<UserRow["role"], string> = { admin: "Administrator", teacher: "Teacher", examiner: "Examiner" };

export function UsersAdmin({ meId, initial }: { meId: string; initial: UserRow[] }) {
  const [users, setUsers] = useState<UserRow[]>(initial);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState("");

  async function load() {
    try {
      setUsers(await api<UserRow[]>("users"));
    } catch {
      setError("We couldn't refresh the list. Please reload the page.");
    }
  }

  async function create(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api<UserRow>("users", {
        method: "POST",
        json: { email: f.get("email"), display_name: f.get("display_name"), role: f.get("role"), password },
      });
      setNotice("Account created. Give them the temporary password yourself; it is not shown again.");
      form.reset();
      setPassword("");
      await load();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not create the account.");
    } finally {
      setBusy(false);
    }
  }

  async function setActive(u: UserRow, active: boolean) {
    setError(null);
    setNotice(null);
    try {
      await api(`users/${u.id}/${active ? "activate" : "deactivate"}`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not change the account.");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>New user</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-6 sm:items-end" aria-label="Create a user">
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="user-email">Email</Label>
              <Input id="user-email" name="email" type="email" required maxLength={320} autoComplete="off" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="user-name">Name</Label>
              <Input id="user-name" name="display_name" required maxLength={200} autoComplete="off" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="user-role">Role</Label>
              <Select id="user-role" name="role" defaultValue="examiner">
                <option value="examiner">Examiner</option>
                <option value="teacher">Teacher</option>
                <option value="admin">Administrator</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="user-password">Temporary password</Label>
              <div className="flex gap-1">
                <Input
                  id="user-password"
                  value={password}
                  onChange={(ev) => setPassword(ev.target.value)}
                  required
                  minLength={12}
                  maxLength={200}
                  autoComplete="off"
                  spellCheck={false}
                />
                <Button type="button" variant="outline" onClick={() => setPassword(temporaryPassword())}>
                  Generate
                </Button>
              </div>
            </div>
            <Button type="submit" disabled={busy}>
              Create user
            </Button>
          </form>
          <p className="mt-2 text-sm text-muted-foreground">At least 12 characters. There is no email: pass the password on yourself, and ask them to keep it private.</p>
        </CardContent>
      </Card>
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      {notice ? <Alert>{notice}</Alert> : null}
      <Card className="py-0">
          <CardContent className="px-0">
            <table className="w-full text-sm">
              <caption className="sr-only">Users</caption>
              <thead className="border-b bg-muted text-left">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">Name</th>
                  <th scope="col" className="px-4 py-3 font-medium">Email</th>
                  <th scope="col" className="px-4 py-3 font-medium">Role</th>
                  <th scope="col" className="px-4 py-3 font-medium">Status</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-b last:border-0" data-testid="user-row">
                    <td className="px-4 py-3 font-medium">{u.display_name}</td>
                    <td className="px-4 py-3">{u.email}</td>
                    <td className="px-4 py-3">{ROLE_LABEL[u.role]}</td>
                    <td className="px-4 py-3">
                      <Badge tone={u.is_active ? "success" : "neutral"}>{u.is_active ? "Active" : "Deactivated"}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {u.id === meId ? (
                        <span className="text-muted-foreground">This is you</span>
                      ) : u.is_active ? (
                        <Button size="sm" variant="outline" onClick={() => void setActive(u, false)} aria-label={`Deactivate ${u.display_name}`}>
                          Deactivate
                        </Button>
                      ) : (
                        <Button size="sm" variant="outline" onClick={() => void setActive(u, true)} aria-label={`Reactivate ${u.display_name}`}>
                          Reactivate
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
      </Card>
    </div>
  );
}
