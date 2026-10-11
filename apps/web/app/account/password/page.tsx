import type { Metadata } from "next";
import Link from "next/link";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiGet, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Change password" };

const MESSAGES: Record<string, string> = {
  current: "The current password is not correct.",
  mismatch: "The two new passwords are not the same.",
  weak: "That password is not accepted. Use at least 12 characters, different from the old one, without your email address.",
  throttled: "Too many attempts. Wait a few minutes, then try again.",
  unavailable: "The service is not reachable right now. Try again in a minute.",
  request: "Some fields are missing or invalid.",
};

/** Outside the main app shell on purpose: someone who must change their password can reach this page and nothing else. */
export default async function PasswordPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const me = await apiGet<Me>("/api/me");
  const message = sp.error ? MESSAGES[sp.error] : undefined;
  const forced = me.must_change_password;
  return (
    <main id="main" className="flex min-h-screen items-center justify-center bg-muted p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h1 className="text-xl font-semibold leading-none">Change your password</h1>
          <CardDescription>
            {forced
              ? "An administrator gave you a temporary password. Choose your own to continue."
              : "Signed in as " + me.email + ". Changing it signs you out everywhere else."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form method="post" action="/api/account/password" className="flex flex-col gap-4">
            {sp.done ? <Alert data-testid="password-changed">Your password was changed. Other sessions were signed out.</Alert> : null}
            {message ? (
              <Alert variant="destructive" id="password-message">
                {message}
              </Alert>
            ) : null}
            <div className="flex flex-col gap-2">
              <Label htmlFor="current_password">{forced ? "Temporary password" : "Current password"}</Label>
              <Input id="current_password" name="current_password" type="password" autoComplete="current-password" required aria-describedby={message ? "password-message" : undefined} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="new_password">New password</Label>
              <Input id="new_password" name="new_password" type="password" autoComplete="new-password" required minLength={12} maxLength={200} />
              <p className="text-sm text-muted-foreground">At least 12 characters, different from the old one, and not containing your email address.</p>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="new_password_again">New password again</Label>
              <Input id="new_password_again" name="new_password_again" type="password" autoComplete="new-password" required minLength={12} maxLength={200} />
            </div>
            <Button type="submit" className="w-full">
              Change password
            </Button>
            {forced ? null : (
              <Link href="/" className="text-center text-sm text-muted-foreground underline underline-offset-4">
                Back to GradeMIND
              </Link>
            )}
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
