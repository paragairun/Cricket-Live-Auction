"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";

type Sponsor = { name: string; category: string; logo_url: string; website: string };
type EventProfile = {
  sport: string;
  tournament_type: string;
  venue_name: string;
  venue_address: string;
  tournament_start_date: string;
  tournament_end_date: string;
  tournament_format: string;
  match_format: string;
  eligibility: string;
  captain_selection: string;
  overseas_player_limit: string;
  playing_xi_rules: string;
  season: string;
  edition_year: string;
  tagline: string;
  title_sponsor: Sponsor;
  other_sponsors: Sponsor[];
  prize_winner: string;
  prize_runner_up: string;
  player_awards: string;
  rules_document_url: string;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  website: string;
  social_links: string;
};

const EMPTY_SPONSOR: Sponsor = { name: "", category: "Associate Sponsor", logo_url: "", website: "" };
const DEFAULTS: EventProfile = {
  sport: "Cricket", tournament_type: "", venue_name: "", venue_address: "",
  tournament_start_date: "", tournament_end_date: "", tournament_format: "",
  match_format: "", eligibility: "", captain_selection: "", overseas_player_limit: "",
  playing_xi_rules: "", season: "", edition_year: "", tagline: "",
  title_sponsor: { ...EMPTY_SPONSOR, category: "Title Sponsor" }, other_sponsors: [],
  prize_winner: "", prize_runner_up: "", player_awards: "", rules_document_url: "",
  contact_name: "", contact_phone: "", contact_email: "", website: "", social_links: "",
};

export default function EventProfileEditor({
  value, onSave, saving, eventId,
}: {
  value?: Record<string, unknown> | null;
  eventId: number;
  onSave: (details: EventProfile) => Promise<void>;
  saving: boolean;
}) {
  const [form, setForm] = useState<EventProfile>(DEFAULTS);
  useEffect(() => {
    const incoming = (value || {}) as Partial<EventProfile>;
    setForm({
      ...DEFAULTS, ...incoming,
      title_sponsor: { ...DEFAULTS.title_sponsor, ...(incoming.title_sponsor || {}) },
      other_sponsors: Array.isArray(incoming.other_sponsors) ? incoming.other_sponsors : [],
    });
  }, [value]);

  const uploadAsset = async (file: File, type: "sponsor-logo" | "rules-document") => {
    const data = new FormData();
    data.append("file", file);
    const response = await api.post(`/organizer/events/${eventId}/profile-assets/${type}`, data, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return response.data.url as string;
  };
  const field = (key: Exclude<keyof EventProfile, "title_sponsor" | "other_sponsors">, label: string, placeholder = "", type = "text") => (
    <label className="block">
      <span className="label">{label}</span>
      <input className="input" type={type} value={String(form[key] ?? "")}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} placeholder={placeholder} />
    </label>
  );
  const sponsorFields = (sponsor: Sponsor, update: (next: Sponsor) => void, title = false) => (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <label className="block"><span className="label">Sponsor name</span><input className="input" value={sponsor.name} onChange={(e) => update({ ...sponsor, name: e.target.value })} placeholder="Company / brand name" /></label>
      {!title && <label className="block"><span className="label">Sponsor category</span><select className="input" value={sponsor.category} onChange={(e) => update({ ...sponsor, category: e.target.value })}><option>Associate Sponsor</option><option>Powered By</option><option>Co-Sponsor</option><option>Official Partner</option><option>Media Partner</option><option>Other</option></select></label>}
      <label className="block"><span className="label">Logo URL</span><input className="input" value={sponsor.logo_url} onChange={(e) => update({ ...sponsor, logo_url: e.target.value })} placeholder="https://..." /><input className="mt-2 block w-full text-xs text-gray-400" type="file" accept="image/*" onChange={async (e) => { const file = e.target.files?.[0]; if (!file) return; try { update({ ...sponsor, logo_url: await uploadAsset(file, "sponsor-logo") }); } catch { alert("Could not upload sponsor logo."); } }} /></label>
      <label className="block"><span className="label">Website</span><input className="input" value={sponsor.website} onChange={(e) => update({ ...sponsor, website: e.target.value })} placeholder="https://..." /></label>
    </div>
  );

  return (
    <div className="space-y-5">
      <section className="card space-y-4">
        <div><h3 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Tournament identity</h3><p className="text-xs text-gray-500 mt-1">These details appear on the Event Overview.</p></div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {field("sport", "Sport", "Cricket")}
          <label className="block"><span className="label">Tournament type</span><select className="input" value={form.tournament_type} onChange={(e) => setForm(f => ({ ...f, tournament_type: e.target.value }))}><option value="">Select type</option>{["Corporate","Society","Club","School","College","Academy","Open","Charity","Other"].map(x => <option key={x}>{x}</option>)}</select></label>
          {field("season", "Season", "Season 1")}
          {field("edition_year", "Edition / Year", "2026")}
          {field("tagline", "Tournament tagline", "Where Legends Rise")}
        </div>
      </section>
      <section className="card space-y-4">
        <h3 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Venue & tournament schedule</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {field("venue_name", "Venue name", "MCA Ground")}
          {field("venue_address", "Venue address / city", "BKC, Mumbai")}
          {field("tournament_start_date", "Tournament start date", "", "date")}
          {field("tournament_end_date", "Tournament end date", "", "date")}
        </div>
      </section>
      <section className="card space-y-4">
        <h3 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Competition format & eligibility</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="block"><span className="label">Tournament format</span><select className="input" value={form.tournament_format} onChange={(e) => setForm(f => ({ ...f, tournament_format: e.target.value }))}><option value="">Select format</option>{["League","Knockout","League + Knockout","Round Robin","Group Stage + Knockout","Other"].map(x => <option key={x}>{x}</option>)}</select></label>
          <label className="block"><span className="label">Match format</span><select className="input" value={form.match_format} onChange={(e) => setForm(f => ({ ...f, match_format: e.target.value }))}><option value="">Select format</option>{["T5","T10","T12","T15","T20","30 Overs","40 Overs","50 Overs","Other"].map(x => <option key={x}>{x}</option>)}</select></label>
          {field("overseas_player_limit", "Overseas player limit", "e.g. 4")}
          {field("captain_selection", "Captain selection", "Pre-assigned / Auction")}
        </div>
        <label className="block"><span className="label">Player eligibility</span><textarea className="input min-h-20" value={form.eligibility} onChange={(e) => setForm(f => ({ ...f, eligibility: e.target.value }))} placeholder="Age, membership, residency or other eligibility requirements" /></label>
        <label className="block"><span className="label">Playing XI rules</span><textarea className="input min-h-20" value={form.playing_xi_rules} onChange={(e) => setForm(f => ({ ...f, playing_xi_rules: e.target.value }))} placeholder="Special playing XI restrictions or composition rules" /></label>
      </section>
      <section className="card space-y-4">
        <h3 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Sponsorship</h3>
        <div className="rounded-xl border border-amber-500/30 p-4 space-y-3">
          <div><p className="font-semibold text-amber-300">Title Sponsor</p><p className="text-xs text-gray-500">Displayed most prominently on the Event Overview.</p></div>
          {sponsorFields(form.title_sponsor, (next) => setForm(f => ({ ...f, title_sponsor: next })), true)}
        </div>
        <div className="flex items-center justify-between"><div><p className="font-semibold">Other Sponsors</p><p className="text-xs text-gray-500">Add associate sponsors, partners and supporting brands.</p></div><button type="button" className="btn-secondary text-sm" onClick={() => setForm(f => ({ ...f, other_sponsors: [...f.other_sponsors, { ...EMPTY_SPONSOR }] }))}>+ Add Sponsor</button></div>
        {form.other_sponsors.map((s, i) => <div key={i} className="rounded-xl border border-gray-800 p-4 space-y-3"><div className="flex justify-between items-center"><p className="text-sm font-medium">Sponsor {i + 1}</p><div className="flex gap-3"><button type="button" className="text-xs text-gray-400" disabled={i === 0} onClick={() => setForm(f => { const a = [...f.other_sponsors]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; return { ...f, other_sponsors: a }; })}>Move up</button><button type="button" className="text-xs text-gray-400" disabled={i === form.other_sponsors.length - 1} onClick={() => setForm(f => { const a = [...f.other_sponsors]; [a[i + 1], a[i]] = [a[i], a[i + 1]]; return { ...f, other_sponsors: a }; })}>Move down</button><button type="button" className="text-xs text-red-400" onClick={() => setForm(f => ({ ...f, other_sponsors: f.other_sponsors.filter((_, n) => n !== i) }))}>Remove</button></div></div>{sponsorFields(s, (next) => setForm(f => ({ ...f, other_sponsors: f.other_sponsors.map((item, n) => n === i ? next : item) })))}</div>)}
      </section>
      <section className="card space-y-4">
        <h3 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Prizes, rules & contact</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {field("prize_winner", "Winner prize", "₹")}
          {field("prize_runner_up", "Runner-up prize", "₹")}
          {field("player_awards", "Player awards", "MVP, Best Batter, Best Bowler")}
          {field("rules_document_url", "Tournament rules PDF URL", "https://...")}<label className="block"><span className="label">Upload tournament rules (PDF)</span><input className="block w-full text-xs text-gray-400" type="file" accept="application/pdf,.pdf" onChange={async (e) => { const file = e.target.files?.[0]; if (!file) return; try { const url = await uploadAsset(file, "rules-document"); setForm(f => ({ ...f, rules_document_url: url })); } catch { alert("Could not upload rules PDF."); } }} /></label>
          {field("contact_name", "Contact person", "Name")}
          {field("contact_phone", "Contact number", "+91...")}
          {field("contact_email", "Contact email", "name@example.com", "email")}
          {field("website", "Tournament website", "https://...")}
          {field("social_links", "Social media links", "Instagram, Facebook, etc.")}
        </div>
      </section>
      <div className="flex justify-end"><button type="button" className="btn-primary" disabled={saving} onClick={() => void onSave(form)}>{saving ? "Saving..." : "Save Event Profile"}</button></div>
    </div>
  );
}
