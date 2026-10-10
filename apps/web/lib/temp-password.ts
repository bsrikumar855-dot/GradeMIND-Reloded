/** A random temporary password for an account an administrator creates (about 100 bits; no look-alike characters). */
const ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";

export function temporaryPassword(random: (n: number) => Uint32Array = (n) => crypto.getRandomValues(new Uint32Array(n)), length = 18): string {
  const limit = Math.floor(0x100000000 / ALPHABET.length) * ALPHABET.length; // reject the biased tail so every character is equally likely
  let out = "";
  while (out.length < length) {
    for (const v of random(length)) {
      if (v < limit && out.length < length) out += ALPHABET[v % ALPHABET.length];
    }
  }
  return out;
}
