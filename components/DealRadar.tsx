"use client";

import { useMemo, useState } from "react";

export type Offer = {
  id: string;
  title: string;
  store: string;
  fsa: string;
  comparisonGroup: string;
  salePrice: string;
  regularPrice: string | null;
  referenceKind: "MSRP" | "retailer regular" | "90-day median" | null;
  unitPrice: string | null;
  unitBasis: "100g" | "100ml" | "roll" | "unit" | null;
  source: "Flyer" | "Online store" | "RFD";
  sourceUrl: string | null;
  validFrom: string | null;
  validTo: string | null;
  verifiedAt: string | null;
  rfdScore: number | null;
  historicalLow: boolean;
  conditions: string[];
};

const money = (value: string | null) =>
  value !== null && Number.isFinite(Number(value))
    ? new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" }).format(Number(value))
    : "—";

function savings(offer: Offer) {
  const regular = offer.regularPrice === null ? NaN : Number(offer.regularPrice);
  const sale = Number(offer.salePrice);
  return offer.referenceKind && Number.isFinite(regular) && regular > sale && sale >= 0
    ? { amount: regular - sale, percent: ((regular - sale) / regular) * 100 }
    : null;
}

function httpsLink(url: string | null) {
  try {
    const parsed = new URL(url ?? "");
    return parsed.protocol === "https:" && !parsed.username && !parsed.password ? parsed.href : null;
  } catch { return null; }
}

function OfferLink({ offer }: { offer: Offer }) {
  const href = httpsLink(offer.sourceUrl);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium text-emerald-800 underline underline-offset-4">View source<span className="sr-only"> for {offer.title} at {offer.store}, opens new tab</span></a> : <span className="text-slate-500">Link unavailable</span>;
}

// Illustrative offer data should be labelled by the caller. Eligibility must also
// be enforced server-side with stock, match, freshness and location verification.
export default function DealRadar({ offers, initialFsa = "N9V", demo = true }: {
  offers: Offer[]; initialFsa?: string; demo?: boolean;
}) {
  const [fsa, setFsa] = useState(initialFsa.toUpperCase());
  const [draftPostal, setDraftPostal] = useState(initialFsa);
  const [postalError, setPostalError] = useState("");
  const [query, setQuery] = useState("");
  const [saved, setSaved] = useState<string[]>([]);
  const now = Date.now();
  const active = offers.filter(o => o.fsa === fsa &&
    (o.validFrom === null || Date.parse(o.validFrom) <= now) &&
    (o.validTo === null || Date.parse(o.validTo) > now));
  const steals = active.filter(o => o.verifiedAt && o.validTo &&
    ((savings(o)?.percent ?? 0) >= 35 || (o.rfdScore ?? 0) > 25)).slice(0, 5);
  const groups = useMemo(() => {
    const result = new Map<string, Offer[]>();
    for (const offer of active) {
      if (query && !`${offer.title} ${offer.store}`.toLowerCase().includes(query.toLowerCase())) continue;
      const key = `${offer.comparisonGroup} · ${offer.unitBasis ?? "quantity unknown"}`;
      result.set(key, [...(result.get(key) ?? []), offer]);
    }
    for (const group of result.values()) group.sort((a, b) => {
      const aPrice = a.unitPrice === null ? Infinity : Number(a.unitPrice);
      const bPrice = b.unitPrice === null ? Infinity : Number(b.unitPrice);
      return aPrice - bPrice;
    });
    return result;
  }, [active, query]);

  const toggle = (id: string) => setSaved(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id]);
  return <main className="min-h-screen bg-stone-50 text-slate-900">
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-5">
        <a href="#" className="text-2xl font-bold tracking-tight">DealRadar <span className="text-emerald-700">CA</span></a>
        <span className="text-sm text-slate-600">Canadian prices. Clear comparisons. <span aria-live="polite">{saved.length} saved this session</span></span>
      </div>
    </header>
    <div className="mx-auto max-w-6xl space-y-10 px-5 py-8">
      {demo && <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Prototype: supplied example data, local text filtering, and session-only saves. No live prices.</p>}
      <form onSubmit={e => {
        e.preventDefault();
        const normalized = draftPostal.toUpperCase().replace(/\s/g, "");
        if (!/^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z](?:\d[ABCEGHJ-NPRSTV-Z]\d)?$/.test(normalized)) {
          setPostalError("Enter a Canadian FSA or postal code, such as N9V or N9V 1A1."); return;
        }
        setFsa(normalized.slice(0, 3)); setPostalError("");
      }} className="flex flex-wrap items-end gap-3">
        <div><label htmlFor="postal" className="mb-1 block text-sm font-medium">Your postal code or FSA</label>
          <input id="postal" autoComplete="postal-code" value={draftPostal} maxLength={7} onChange={e => setDraftPostal(e.target.value)} aria-invalid={!!postalError} aria-describedby="postal-help postal-error" className="rounded-lg border border-slate-300 bg-white px-3 py-2 uppercase focus-visible:outline-emerald-700" /></div>
        <button className="rounded-lg bg-emerald-800 px-4 py-2 font-medium text-white">Update location</button>
        <p id="postal-help" className="text-sm text-slate-600">Approximate flyer area: {fsa}. Confirm your store before purchasing.</p>
        <p id="postal-error" role="alert" className="w-full text-sm text-red-700">{postalError}</p>
      </form>
      <section aria-labelledby="steals-title">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2"><h1 id="steals-title" className="text-3xl font-bold tracking-tight">Today’s verified steals</h1><span className="text-sm text-slate-600">Savings with a source you can check</span></div>
        {steals.length ? <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{steals.map(offer => {
          const saving = savings(offer);
          return <article key={offer.id} className="rounded-2xl border border-emerald-200 bg-white p-5">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-emerald-800">{offer.historicalLow ? "Observed 90-day low" : offer.source === "Flyer" ? "Flyer deal" : "Verified price"}</p>
            <h2 className="text-lg font-semibold">{offer.title}</h2><p className="mt-1 text-sm text-slate-600">{offer.store}</p>
            <p className="mt-4 text-3xl font-bold">{money(offer.salePrice)}</p>
            {saving && <p className="mt-1 font-medium text-emerald-800">Save {money(String(saving.amount))} ({saving.percent.toFixed(0)}% off {offer.referenceKind})</p>}
            <p className="my-3 text-sm text-slate-600">Valid until {offer.validTo} · {offer.source}</p>
            <p className="mb-3 text-xs text-slate-500">Verified {offer.verifiedAt}</p>
            {offer.conditions.length > 0 && <p className="mb-3 text-sm">{offer.conditions.join(" · ")}</p>}
            <div className="flex items-center justify-between gap-3"><OfferLink offer={offer} /><button onClick={() => toggle(offer.id)} aria-pressed={saved.includes(offer.id)} aria-label={`${saved.includes(offer.id) ? "Unsave" : "Save"} ${offer.title}`} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">{saved.includes(offer.id) ? "Saved" : "Save"}</button></div>
          </article>;
        })}</div> : <p className="rounded-xl border border-slate-200 bg-white p-5 text-slate-600">No verified steals in the supplied data for {fsa}.</p>}
      </section>
      <section aria-labelledby="compare-title">
        <h2 id="compare-title" className="text-2xl font-bold">Find the better price</h2>
        <label htmlFor="search" className="mt-4 block text-sm font-medium">Search supplied products or stores</label>
        <input id="search" type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Try chicken breast, Charmin, or Dyson V8" className="my-2 w-full rounded-xl border border-slate-300 bg-white p-4 focus-visible:outline-emerald-700" />
        <p className="mb-5 text-sm text-slate-600">Lowest unit price first within each comparable group. All prices in CAD; conditions and fees may apply.</p>
        {[...groups].map(([key, group]) => <div key={key} className="mb-6 overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full min-w-[850px] text-left text-sm"><caption className="px-4 py-3 text-left font-semibold">{key}</caption>
            <thead className="bg-stone-100"><tr>{["Store", "Product match", "Sale price", "Regular price", "Unit price", "Validity / source", "Link"].map(label => <th key={label} scope="col" className="p-4">{label}</th>)}</tr></thead>
            <tbody>{group.map(offer => <tr key={offer.id} className="border-t border-slate-100">
              <th scope="row" className="p-4 font-medium">{offer.store}</th><td className="p-4">{offer.title}<p className="mt-1 text-xs text-slate-500">{offer.conditions.join(" · ")}</p></td>
              <td className="p-4 font-semibold">{money(offer.salePrice)}</td><td className="p-4">{money(offer.regularPrice)}</td>
              <td className="p-4">{offer.unitPrice !== null && offer.unitBasis ? `${money(offer.unitPrice)}/${offer.unitBasis}` : "Quantity unknown"}</td>
              <td className="p-4">{offer.source}<p className="mt-1 text-xs text-slate-500">{offer.validTo ? `Until ${offer.validTo}` : "Expiry unknown"}</p></td><td className="p-4"><OfferLink offer={offer} /></td>
            </tr>)}</tbody>
          </table>
        </div>)}
        {groups.size === 0 && <p role="status" className="py-6 text-slate-600">No matching products in the supplied data. Try another term or location.</p>}
      </section>
      <footer className="border-t border-slate-200 py-6 text-sm text-slate-600">Buy what you need. Check the retailer’s current price, stock, and purchase conditions.</footer>
    </div>
  </main>;
}
