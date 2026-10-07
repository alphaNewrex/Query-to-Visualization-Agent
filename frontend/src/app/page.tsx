import { QueryPage } from "@/components/query-page";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">ClinicalTrials.gov Query-to-Visualization Agent</h1>
        <p className="text-sm text-muted-foreground">Ask a question about clinical trials and get a chart with its sources.</p>
      </header>
      <QueryPage />
    </main>
  );
}
