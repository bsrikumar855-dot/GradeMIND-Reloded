import type { Metadata } from "next";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const metadata: Metadata = { title: "Sign in" };

const MESSAGES: Record<string, string> = {
  invalid: "The email or password is not correct.",
  expired: "Your session has ended. Sign in again.",
  unavailable: "The service is not reachable right now. Try again in a minute.",
  request: "Some fields are missing or invalid.",
  throttled: "Too many sign-in attempts. Wait a few minutes, then try again.",
};

/** A plain HTML form posting to a same-origin route handler: works without JavaScript; the token never reaches the page. */
export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const message = sp.error ? MESSAGES[sp.error] : sp.expired ? MESSAGES.expired : undefined;
  return (
    <main id="main" className="flex min-h-screen items-center justify-center bg-muted p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <h1 className="text-xl font-semibold leading-none">Sign in to GradeMIND</h1>
          <CardDescription>Use the account your administrator created for you.</CardDescription>
        </CardHeader>
        <CardContent>
          <form method="post" action="/api/session" className="flex flex-col gap-4">
            {message ? (
              <Alert variant={sp.error ? "destructive" : "default"} id="login-message">
                {message}
              </Alert>
            ) : null}
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" autoComplete="username" required aria-describedby={message ? "login-message" : undefined} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" name="password" type="password" autoComplete="current-password" required />
            </div>
            <Button type="submit" className="w-full">
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
