import type { ButtonHTMLAttributes } from "react";

export function Button({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`rounded border border-zinc-400 px-3 py-1 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800 ${className}`}
    />
  );
}
