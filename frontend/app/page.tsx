const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getBackendStatus(): Promise<string> {
  try {
    const response = await fetch(`${API_URL}/health`, { cache: "no-store" });
    return response.ok ? "ok" : `error (${response.status})`;
  } catch {
    return "unreachable";
  }
}

export default async function Home() {
  const backendStatus = await getBackendStatus();

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 p-16">
      <h1 className="text-3xl font-semibold tracking-tight">business-platform</h1>
      <p className="text-zinc-600 dark:text-zinc-400">
        Backend: <span className="font-mono">{backendStatus}</span>
      </p>
    </main>
  );
}
