"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import api, { describeError } from "@/lib/api";
import { derivePublishState, isDraftState } from "@/lib/eventStatus";
import UserSearchInvite from "@/components/UserSearchInvite";

/** A roster player plus their state in this event. New in 2.0.0. */
interface EventPlayer {
  id: number;               // players.id
  name: string;
  email: string | null;
  phone: string | null;
  age: number | null;
  profile_photo: string | null;
  notes: string | null;
  batting_rating: number;
  bowling_rating: number;
  fielding_rating: number;
  created_at: string;
  auction_player_id: number | null;
  base_price: number | null;
  category_id: number | null;
  status: string | null;
  sold_price: number | null;
  sold_to_team_id: number | null;
}

interface Team {
  id: number;
  name: string;
  color: string;
  logo?: string | null;
  captain_id: number | null;
  budget: number;
  max_players: number;
  players: unknown[];
}

interface EventDetail {
  id: number;
  name: string;
  status: string;
  auctioneer_id: number | null;
  scheduled_at: string | null;
  team_budget: number;
  team_max_players: number;
  player_base_price: number;
  logo: string | null;
  background_image?: string | null;
}

interface Readiness {
  checks: Record<string, boolean>;
  ready: boolean;
  teams_count: number;
  teams_with_captain: number;
  players_count: number;
  auctioneer_id: number | null;
}

interface BidSlab {
  id: number;
  event_id: number;
  min_value: number;
  max_value: number;
  increment: number;
  sort_order: number;
}

interface PlayerCategory {
  id: number;
  event_id: number;
  name: string;
  base_price: number;
  sort_order: number;
  auction_first: boolean;
}

const CHECK_LABELS: Record<string, string> = {
  organizer_assigned: "Organizer assigned",
  auctioneer_assigned: "Auctioneer assigned",
  teams_created: "At least 2 teams created",
  all_teams_have_captain: "All teams have a captain",
  players_added: "At least 1 player added",
};

// ─── Teams Tab ────────────────────────────────────────────────────────────────

const COLOR_PRESETS = [
  { hex: "#1E40AF", label: "Royal Blue" },
  { hex: "#7C3AED", label: "Purple" },
  { hex: "#DC2626", label: "Red" },
  { hex: "#059669", label: "Green" },
  { hex: "#D97706", label: "Gold" },
  { hex: "#DB2777", label: "Pink" },
  { hex: "#0891B2", label: "Teal" },
  { hex: "#EA580C", label: "Orange" },
  { hex: "#4F46E5", label: "Indigo" },
  { hex: "#65A30D", label: "Lime" },
  { hex: "#9D174D", label: "Maroon" },
  { hex: "#0F766E", label: "Dark Teal" },
];

function hexToRgb(hex: string) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return { r, g, b };
}

function isDark(hex: string) {
  const { r, g, b } = hexToRgb(hex);
  return (r * 299 + g * 587 + b * 114) / 1000 < 128;
}

function TeamColorPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (hex: string) => void;
}) {
  return (
    <div>
      <label className="label">Team Color</label>
      <div className="flex flex-wrap gap-2 mb-3">
        {COLOR_PRESETS.map((c) => (
          <button
            key={c.hex}
            type="button"
            title={c.label}
            onClick={() => onChange(c.hex)}
            className="w-8 h-8 rounded-lg transition-all hover:scale-110 border-2"
            style={{
              backgroundColor: c.hex,
              borderColor: value === c.hex ? "white" : "transparent",
              boxShadow: value === c.hex ? `0 0 0 2px ${c.hex}` : "none",
            }}
          />
        ))}
        <label
          title="Custom color"
          className="w-8 h-8 rounded-lg border-2 border-dashed border-gray-600 hover:border-gray-400 cursor-pointer flex items-center justify-center text-gray-400 text-xs transition-colors relative overflow-hidden"
        >
          <span>+</span>
          <input
            type="color"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
          />
        </label>
      </div>
      <div
        className="flex items-center gap-3 rounded-xl px-4 py-2.5"
        style={{ backgroundColor: value }}
      >
        <span
          className="text-sm font-bold"
          style={{ color: isDark(value) ? "white" : "#111" }}
        >
          Team Name Preview
        </span>
        <span
          className="text-xs ml-auto font-mono"
          style={{ color: isDark(value) ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.5)" }}
        >
          {value}
        </span>
      </div>
    </div>
  );
}

function TeamsTab({
  eventId,
  teams,
  rosterPlayers,
  teamForm,
  setTeamForm,
  onCreateTeam,
  onAssignCaptain,
  onUpdateTeam,
  onDeleteTeam,
  onUploadTeamLogo,
  onDeleteTeamLogo,
  editable,
}: {
  eventId: number;
  teams: Team[];
  rosterPlayers: EventPlayer[];
  teamForm: { name: string; color: string; budget?: string; max_players?: string; logoFile?: File | null };
  setTeamForm: (f: { name: string; color: string; budget?: string; max_players?: string; logoFile?: File | null }) => void;
  onCreateTeam: (e: React.FormEvent) => Promise<void>;
  onAssignCaptain: (teamId: number, captainId: string) => void;
  onUpdateTeam: (teamId: number, data: { name?: string; color?: string }) => void;
  onDeleteTeam: (teamId: number) => void;
  onUploadTeamLogo: (teamId: number, file: File) => Promise<void>;
  onDeleteTeamLogo: (teamId: number) => Promise<void>;
  editable: boolean;
}) {
  const [showForm, setShowForm] = useState(editable && teams.length === 0);
  const [editingTeamId, setEditingTeamId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState({ name: "", color: "" });
  const [uploadingTeamId, setUploadingTeamId] = useState<number | null>(null);
  const [newTeamLogoPreview, setNewTeamLogoPreview] = useState<string | null>(null);

  const playerMap = Object.fromEntries(rosterPlayers.map((p) => [p.id, p]));
  const assignedCaptainIds = new Set(
    teams.filter((t) => t.captain_id !== null).map((t) => t.captain_id as number)
  );

  const handleSubmit = async (e: React.FormEvent) => {
    await onCreateTeam(e);
    if (newTeamLogoPreview) {
      URL.revokeObjectURL(newTeamLogoPreview);
      setNewTeamLogoPreview(null);
    }
    setShowForm(false);
  };

  return (
    <div className="space-y-4">
      {/* Existing teams */}
      {teams.length > 0 && (
        <div className="space-y-3">
          {teams.map((team) => {
            const captain = team.captain_id ? playerMap[team.captain_id] : null;
            const textColor = isDark(team.color) ? "white" : "#111827";
            const subtextColor = isDark(team.color) ? "rgba(255,255,255,0.65)" : "rgba(0,0,0,0.5)";
            const isEditing = editingTeamId === team.id;

            const availableCaptains = rosterPlayers.filter(
              (p) => !assignedCaptainIds.has(p.id) || p.id === team.captain_id
            );

            return (
              <div key={team.id} className="bg-gray-900 border border-gray-800 rounded-2xl overflow-hidden">
                {/* Colored header */}
                <div
                  className="px-5 py-4 flex items-center justify-between gap-3"
                  style={{ backgroundColor: team.color }}
                >
                  <div className="flex items-center gap-3.5 flex-1 min-w-0">
                    {/* Team Logo Upload / Preview */}
                    <div className="relative group shrink-0">
                      <label
                        title={editable ? "Upload team logo" : team.name}
                        className={`w-14 h-14 rounded-xl overflow-hidden bg-black/25 border border-white/30 flex items-center justify-center ${
                          editable ? "cursor-pointer hover:border-white" : ""
                        }`}
                      >
                        {uploadingTeamId === team.id ? (
                          <span className="text-[10px] text-white">...</span>
                        ) : team.logo ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={team.logo} alt={team.name} className="w-full h-full object-cover" />
                        ) : (
                          <span className="text-xs font-bold text-white/80 text-center leading-tight px-1">
                            {editable ? "+ Logo" : team.name.slice(0, 2).toUpperCase()}
                          </span>
                        )}
                        {editable && (
                          <input
                            type="file"
                            accept="image/*"
                            className="hidden"
                            disabled={uploadingTeamId === team.id}
                            onChange={async (e) => {
                              const file = e.target.files?.[0];
                              if (!file) return;
                              setUploadingTeamId(team.id);
                              try {
                                await onUploadTeamLogo(team.id, file);
                              } finally {
                                setUploadingTeamId(null);
                              }
                            }}
                          />
                        )}
                      </label>
                      {editable && team.logo && (
                        <button
                          type="button"
                          onClick={() => onDeleteTeamLogo(team.id)}
                          title="Remove team logo"
                          className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-600 hover:bg-red-500 text-white rounded-full text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          ×
                        </button>
                      )}
                    </div>

                    {isEditing ? (
                      <div className="flex-1 mr-3">
                        <input
                          className="input text-sm mb-2"
                          value={editForm.name}
                          onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                          placeholder="Team name"
                          autoFocus
                        />
                        <div className="flex flex-wrap gap-1.5">
                          {COLOR_PRESETS.map((c) => (
                            <button
                              key={c.hex}
                              type="button"
                              onClick={() => setEditForm({ ...editForm, color: c.hex })}
                              className="w-6 h-6 rounded-md border-2 transition-all"
                              style={{
                                backgroundColor: c.hex,
                                borderColor: editForm.color === c.hex ? "white" : "transparent",
                              }}
                            />
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="min-w-0">
                        <h3 className="font-bold text-lg truncate" style={{ color: textColor }}>{team.name}</h3>
                        <p className="text-xs mt-0.5" style={{ color: subtextColor }}>
                          Budget: {team.budget.toLocaleString()} · Max {team.max_players} players
                        </p>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {isEditing ? (
                      <>
                        <button
                          type="button"
                          className="text-xs px-3 py-1.5 rounded-lg font-medium bg-green-500 text-white hover:bg-green-600"
                          onClick={() => {
                            onUpdateTeam(team.id, editForm);
                            setEditingTeamId(null);
                          }}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className="text-xs px-3 py-1.5 rounded-lg font-medium bg-gray-700 text-white hover:bg-gray-600"
                          onClick={() => setEditingTeamId(null)}
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {team.captain_id ? (
                          <span className="text-xs px-2.5 py-1 rounded-full font-medium"
                            style={{ backgroundColor: "rgba(255,255,255,0.2)", color: textColor }}>
                            ✓ Captain set
                          </span>
                        ) : (
                          <span className="text-xs px-2.5 py-1 rounded-full"
                            style={{ backgroundColor: "rgba(0,0,0,0.25)", color: isDark(team.color) ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.5)" }}>
                            No captain yet
                          </span>
                        )}
                        {editable && (
                          <>
                            <button
                              type="button"
                              className="text-xs px-2 py-1 rounded-lg hover:bg-white/20 transition-colors"
                              style={{ color: textColor }}
                              onClick={() => {
                                setEditForm({ name: team.name, color: team.color });
                                setEditingTeamId(team.id);
                              }}
                              title="Edit team"
                            >
                              ✏️
                            </button>
                            <button
                              type="button"
                              className="text-xs px-2 py-1 rounded-lg hover:bg-red-500/30 transition-colors"
                              style={{ color: textColor }}
                              onClick={() => {
                                if (confirm(`Delete team "${team.name}"?`)) {
                                  onDeleteTeam(team.id);
                                }
                              }}
                              title="Delete team"
                            >
                              🗑️
                            </button>
                          </>
                        )}
                      </>
                    )}
                  </div>
                </div>

                {/* Captain section */}
                <div className="px-5 py-4">
                  <label className="label">Assign Captain</label>
                  {captain && (
                    <div className="flex items-center gap-3 mb-3 bg-gray-800 rounded-xl px-3 py-2.5">
                      <div className="w-9 h-9 rounded-full overflow-hidden bg-gray-700 shrink-0 flex items-center justify-center">
                        {captain.profile_photo
                          // eslint-disable-next-line @next/next/no-img-element
                          ? <img src={captain.profile_photo} alt="" className="w-full h-full object-cover" />
                          : <span>👤</span>}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium">{captain.name}</p>
                        <p className="text-xs text-gray-500">{captain.email}</p>
                      </div>
                      <span className="text-xs font-medium" style={{ color: team.color }}>Captain</span>
                    </div>
                  )}

                  {editable ? (
                    <select
                      className="input text-sm"
                      value={team.captain_id?.toString() || ""}
                      onChange={(e) => onAssignCaptain(team.id, e.target.value)}
                    >
                      <option value="">{captain ? "Change captain..." : "— Select captain —"}</option>
                      {availableCaptains.map((p) => (
                        <option key={p.id} value={p.id}>{p.name} · {p.email}</option>
                      ))}
                    </select>
                  ) : (
                    !captain && (
                      <p className="text-xs text-gray-600 mt-1">
                        Unpublish the event to assign a captain.
                      </p>
                    )
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Add team button / form */}
      {editable && (
        !showForm ? (
          <button
            type="button"
            onClick={() => setShowForm(true)}
            className="w-full border-2 border-dashed border-gray-700 hover:border-amber-500/50 hover:bg-amber-500/5 rounded-2xl py-6 text-gray-500 hover:text-amber-400 transition-all flex items-center justify-center gap-2 text-sm font-medium"
          >
            <span className="text-xl leading-none">＋</span>
            Add {teams.length > 0 ? "Another" : "First"} Team
          </button>
        ) : (
          <form
            onSubmit={handleSubmit}
            className="bg-gray-900 border-2 border-amber-500/30 rounded-2xl p-5 space-y-4"
          >
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-white">New Team</h3>
              {teams.length > 0 && (
                <button type="button" onClick={() => setShowForm(false)}
                  className="text-gray-500 hover:text-white text-xl leading-none">×</button>
              )}
            </div>

            <div className="flex items-center gap-4">
              <label className="w-20 h-20 shrink-0 rounded-xl border-2 border-dashed border-gray-700 hover:border-amber-500/50 hover:bg-amber-500/5 cursor-pointer flex flex-col items-center justify-center text-gray-500 hover:text-amber-400 overflow-hidden transition-all">
                {newTeamLogoPreview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={newTeamLogoPreview} alt="Team logo preview" className="w-full h-full object-cover" />
                ) : (
                  <>
                    <span className="text-xl mb-0.5">🛡️</span>
                    <span className="text-[10px]">Team Logo</span>
                  </>
                )}
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0] ?? null;
                    setTeamForm({ ...teamForm, logoFile: file });
                    if (newTeamLogoPreview) URL.revokeObjectURL(newTeamLogoPreview);
                    setNewTeamLogoPreview(file ? URL.createObjectURL(file) : null);
                  }}
                />
              </label>

              <div className="flex-1">
                <label className="label">Team Name *</label>
                <input
                  className="input"
                  value={teamForm.name}
                  onChange={(e) => setTeamForm({ ...teamForm, name: e.target.value })}
                  placeholder="e.g. Mumbai Indians"
                  required
                  autoFocus
                />
              </div>
            </div>

            <TeamColorPicker
              value={teamForm.color}
              onChange={(hex) => setTeamForm({ ...teamForm, color: hex })}
            />

            <div className="flex gap-3 pt-1">
              <button type="submit" className="btn-primary flex-1">Create Team</button>
              {teams.length > 0 && (
                <button type="button" className="btn-secondary px-5" onClick={() => setShowForm(false)}>Cancel</button>
              )}
            </div>
          </form>
        )
      )}
    </div>
  );
}

// ─── Players Tab ──────────────────────────────────────────────────────────────

function toLocalDatetimeValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function PlayersTab({
  eventId,
  players,
  onChanged,
  onToast,
  editable,
  eventStatus,
  categories,
  teams,
  eventBasePrice,
}: {
  eventId: number;
  players: EventPlayer[];
  onChanged: () => void;
  onToast: (type: "ok" | "err", text: string) => void;
  editable: boolean;
  eventStatus: string | undefined;
  categories: PlayerCategory[];
  teams: Team[];
  eventBasePrice: number;
}) {
  const emptyForm = {
    name: "",
    phone: "",
    email: "",
    age: "",
    batting_rating: 5,
    bowling_rating: 5,
    fielding_rating: 5,
    category_id: null as number | null,
    base_price: "" as string,
  };

  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "sold" | "unsold" | "pending">("all");
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importReport, setImportReport] = useState<{
    imported: number;
    skipped: number;
    errors: string[];
  } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [busyRow, setBusyRow] = useState<number | null>(null);

  const captainIds = new Set(
    teams.map((t) => t.captain_id).filter((id): id is number => id !== null)
  );
  const teamNameById = Object.fromEntries(teams.map((t) => [t.id, t.name]));

  let visible = players;
  if (filter !== "all") visible = visible.filter((p) => (p.status ?? "pending") === filter);
  if (search.trim()) {
    const q = search.trim().toLowerCase();
    visible = visible.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.phone ?? "").includes(q) ||
        (p.email ?? "").toLowerCase().includes(q)
    );
  }

  const counts = {
    all: players.length,
    pending: players.filter((p) => (p.status ?? "pending") === "pending").length,
    sold: players.filter((p) => p.status === "sold").length,
    unsold: players.filter((p) => p.status === "unsold").length,
  };

  const resolvedBasePrice = (categoryId: number | null) => {
    const cat = categories.find((c) => c.id === categoryId);
    if (cat && cat.base_price > 0) return cat.base_price;
    return eventBasePrice;
  };

  const categoryGoverns = (categoryId: number | null) => {
    const cat = categories.find((c) => c.id === categoryId);
    return !!cat && cat.base_price > 0;
  };

  const openAdd = () => {
    setForm(emptyForm);
    setEditingId(null);
    setPhotoFile(null);
    setPhotoPreview(null);
    setAddOpen(true);
  };

  const openEdit = (player: EventPlayer) => {
    setForm({
      name: player.name,
      phone: player.phone ?? "",
      email: player.email ?? "",
      age: player.age ? String(player.age) : "",
      batting_rating: player.batting_rating,
      bowling_rating: player.bowling_rating,
      fielding_rating: player.fielding_rating,
      category_id: player.category_id ?? null,
      base_price: player.base_price ? String(player.base_price) : "",
    });
    setEditingId(player.auction_player_id ?? null);
    setPhotoFile(null);
    setPhotoPreview(player.profile_photo ?? null);
    setAddOpen(true);
  };

  const uploadPlayerPhoto = async (auctionPlayerId: number, file: File) => {
    setUploadingPhoto(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post(
        `/organizer/events/${eventId}/players/${auctionPlayerId}/photo`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } }
      );
      if (data?.profile_photo) {
        setPhotoPreview(data.profile_photo);
      }
      onToast("ok", "Player photo uploaded");
      onChanged();
    } catch (e: unknown) {
      onToast("err", describeError(e).message);
    } finally {
      setUploadingPhoto(false);
    }
  };

  const deletePlayerPhoto = async (auctionPlayerId: number) => {
    setUploadingPhoto(true);
    try {
      await api.delete(`/organizer/events/${eventId}/players/${auctionPlayerId}/photo`);
      setPhotoPreview(null);
      setPhotoFile(null);
      onToast("ok", "Player photo removed");
      onChanged();
    } catch (e: unknown) {
      onToast("err", describeError(e).message);
    } finally {
      setUploadingPhoto(false);
    }
  };

  const save = async () => {
    if (!form.name.trim()) {
      onToast("err", "A player needs a name");
      return;
    }
    setSaving(true);
    try {
      const payload: Record<string, unknown> = { name: form.name.trim() };
      if (form.phone.trim()) payload.phone = form.phone.trim();
      if (form.email.trim()) payload.email = form.email.trim();
      if (form.age.trim()) payload.age = Number(form.age);
      payload.batting_rating = form.batting_rating;
      payload.bowling_rating = form.bowling_rating;
      payload.fielding_rating = form.fielding_rating;

      if (editingId) {
        await api.patch(`/organizer/events/${eventId}/players/${editingId}`, payload);
        const governed = categoryGoverns(form.category_id);
        const basePrice =
          !governed && form.base_price.trim() ? Number(form.base_price) : undefined;
        await api.patch(
          `/organizer/events/${eventId}/players/${editingId}/auction-settings`,
          {
            ...(basePrice !== undefined ? { base_price: basePrice } : {}),
            category_id: form.category_id,
          }
        );
        if (photoFile) {
          await uploadPlayerPhoto(editingId, photoFile);
        }
        onToast("ok", `${form.name.trim()} updated`);
      } else {
        const params = new URLSearchParams();
        if (!categoryGoverns(form.category_id) && form.base_price.trim()) {
          params.set("base_price", form.base_price.trim());
        }
        if (form.category_id !== null) params.set("category_id", String(form.category_id));
        const { data: created } = await api.post(
          `/organizer/events/${eventId}/players?${params.toString()}`,
          payload
        );
        const newAuctionPlayerId = created?.auction_player_id ?? created?.id;
        if (photoFile && newAuctionPlayerId) {
          await uploadPlayerPhoto(newAuctionPlayerId, photoFile);
        }
        onToast("ok", `${form.name.trim()} added`);
      }
      setAddOpen(false);
      setForm(emptyForm);
      setEditingId(null);
      setPhotoFile(null);
      setPhotoPreview(null);
      onChanged();
    } catch (e: unknown) {
      onToast("err", describeError(e).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (player: EventPlayer) => {
    if (!player.auction_player_id) return;
    if (!confirm(`Remove ${player.name} from this auction?`)) return;
    setBusyRow(player.auction_player_id);
    try {
      await api.delete(`/organizer/events/${eventId}/players/${player.auction_player_id}`);
      onToast("ok", `${player.name} removed`);
      onChanged();
    } catch (e: unknown) {
      onToast("err", describeError(e).message);
    } finally {
      setBusyRow(null);
    }
  };

  const runImport = async () => {
    if (!csvFile) return;
    setImporting(true);
    setImportReport(null);
    try {
      const formData = new FormData();
      formData.append("file", csvFile);
      const { data } = await api.post(
        `/organizer/events/${eventId}/import-players`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } }
      );
      setImportReport(data);
      onToast("ok", `Imported ${data.imported} player(s)`);
      setCsvFile(null);
      onChanged();
    } catch (e: unknown) {
      onToast("err", describeError(e).message);
    } finally {
      setImporting(false);
    }
  };

  const exportXlsx = async () => {
    setExporting(true);
    try {
      const res = await api.get(`/organizer/events/${eventId}/players/export-xlsx`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `players-event-${eventId}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      onToast("err", describeError(e).message);
    } finally {
      setExporting(false);
    }
  };

  const statusChip = (player: EventPlayer) => {
    const status = player.status ?? "pending";
    const map: Record<string, string> = {
      pending: "bg-gray-700/50 text-gray-300",
      active: "bg-amber-500/20 text-amber-300",
      sold: "bg-green-500/20 text-green-300",
      unsold: "bg-red-500/20 text-red-300",
    };
    return (
      <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[status] ?? map.pending}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name, phone or email"
          className="flex-1 min-w-[220px] bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm"
        />
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value as typeof filter)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
        >
          <option value="all">All ({counts.all})</option>
          <option value="pending">Pending ({counts.pending})</option>
          <option value="sold">Sold ({counts.sold})</option>
          <option value="unsold">Unsold ({counts.unsold})</option>
        </select>

        {editable && (
          <>
            <button
              onClick={openAdd}
              className="bg-amber-500 hover:bg-amber-400 text-black font-semibold px-4 py-2 rounded-lg text-sm"
            >
              + Add player
            </button>
            <button
              onClick={() => setImportOpen(true)}
              className="bg-gray-800 hover:bg-gray-700 border border-gray-700 px-4 py-2 rounded-lg text-sm"
            >
              Import CSV
            </button>
          </>
        )}
        <button
          onClick={exportXlsx}
          disabled={players.length === 0 || exporting}
          className="bg-gray-800 hover:bg-gray-700 border border-gray-700 px-4 py-2 rounded-lg text-sm disabled:opacity-40"
        >
          {exporting ? "Exporting…" : "Export XLSX"}
        </button>
      </div>

      {/* Roster */}
      {players.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-gray-800 rounded-xl">
          <p className="text-4xl mb-3">🏏</p>
          <p className="text-gray-400 font-medium">No players yet.</p>
          <p className="text-sm text-gray-600 mt-1">
            A name is all you need — phone, email and ratings are optional.
          </p>
          {editable && (
            <button
              onClick={openAdd}
              className="mt-4 bg-amber-500 hover:bg-amber-400 text-black font-semibold px-5 py-2 rounded-lg text-sm"
            >
              Add the first player
            </button>
          )}
        </div>
      ) : (
        <div className="border border-gray-800 rounded-xl overflow-hidden">
          {visible.map((player) => {
            const isCaptain = captainIds.has(player.id);
            return (
              <div
                key={player.id}
                className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 last:border-0 hover:bg-gray-900/50"
              >
                <div className="w-10 h-10 rounded-full bg-gray-800 overflow-hidden flex items-center justify-center text-sm font-semibold shrink-0">
                  {player.profile_photo ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={player.profile_photo}
                      alt=""
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    player.name.charAt(0).toUpperCase()
                  )}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium truncate">{player.name}</p>
                    {isCaptain && (
                      <span className="px-2 py-0.5 rounded text-xs bg-blue-500/20 text-blue-300">
                        captain
                      </span>
                    )}
                    {statusChip(player)}
                  </div>
                  <p className="text-xs text-gray-500 truncate">
                    {[player.phone, player.email].filter(Boolean).join(" · ") || "No contact details"}
                    {" · "}
                    bat {player.batting_rating} / bowl {player.bowling_rating} / field{" "}
                    {player.fielding_rating}
                  </p>
                </div>

                <div className="text-right shrink-0">
                  {player.status === "sold" ? (
                    <>
                      <p className="text-sm font-semibold text-green-400">
                        ₹{(player.sold_price ?? 0).toLocaleString("en-IN")}
                      </p>
                      <p className="text-xs text-gray-500">
                        {player.sold_to_team_id
                          ? teamNameById[player.sold_to_team_id] ?? "—"
                          : "—"}
                      </p>
                    </>
                  ) : (
                    <p className="text-xs text-gray-500">
                      base ₹{(player.base_price ?? 0).toLocaleString("en-IN")}
                    </p>
                  )}
                </div>

                {editable && (
                  <div className="flex gap-1 shrink-0">
                    <button
                      onClick={() => openEdit(player)}
                      className="px-2 py-1 text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => remove(player)}
                      disabled={busyRow === player.auction_player_id}
                      className="px-2 py-1 text-xs bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-300 rounded disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </div>
                )}
              </div>
            );
          })}

        </div>
      )}

      <p className="text-xs text-gray-600">
        {players.length} player{players.length === 1 ? "" : "s"} in this auction
        {eventStatus === "completed" && " · auction complete"}
      </p>

      {/* Add / edit modal */}
      {addOpen && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-bold mb-1">
              {editingId ? "Edit player" : "Add player"}
            </h3>
            <p className="text-xs text-gray-500 mb-5">
              Only the name is required.
            </p>

            <div className="space-y-4">
              {/* Player Photo Upload */}
              <div>
                <label className="block text-xs text-gray-400 mb-1.5">Player Photo</label>
                <div className="flex items-center gap-4">
                  <div className="relative group">
                    <label className="w-20 h-20 rounded-xl border-2 border-dashed border-gray-700 hover:border-amber-500/50 hover:bg-amber-500/5 cursor-pointer flex flex-col items-center justify-center text-gray-500 hover:text-amber-400 overflow-hidden transition-all">
                      {uploadingPhoto ? (
                        <span className="text-xs">Uploading...</span>
                      ) : photoPreview ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={photoPreview} alt="Player preview" className="w-full h-full object-cover" />
                      ) : (
                        <>
                          <span className="text-2xl mb-1">📷</span>
                          <span className="text-[10px]">Add Photo</span>
                        </>
                      )}
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        disabled={uploadingPhoto}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          if (editingId) {
                            uploadPlayerPhoto(editingId, file);
                          } else {
                            setPhotoFile(file);
                            setPhotoPreview(URL.createObjectURL(file));
                          }
                        }}
                      />
                    </label>
                    {photoPreview && editingId && (
                      <button
                        type="button"
                        onClick={() => deletePlayerPhoto(editingId)}
                        className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 hover:bg-red-600 rounded-full text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                        title="Remove photo"
                      >
                        ×
                      </button>
                    )}
                  </div>
                  <div className="text-xs text-gray-500">
                    <p>Upload a portrait or action shot.</p>
                    <p>Displayed on the auctioneer &amp; broadcast stage.</p>
                  </div>
                </div>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Name <span className="text-amber-400">*</span>
                </label>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Ramesh Kumar"
                  autoFocus
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Phone</label>
                  <input
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    placeholder="Optional"
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Age</label>
                  <input
                    type="number"
                    value={form.age}
                    onChange={(e) => setForm({ ...form, age: e.target.value })}
                    placeholder="Optional"
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">Email</label>
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="Optional — players do not log in"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div className="grid grid-cols-3 gap-3">
                {(["batting_rating", "bowling_rating", "fielding_rating"] as const).map(
                  (field) => (
                    <div key={field}>
                      <label className="block text-xs text-gray-400 mb-1 capitalize">
                        {field.replace("_rating", "")}
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={10}
                        step={0.5}
                        value={form[field]}
                        onChange={(e) =>
                          setForm({ ...form, [field]: Number(e.target.value) })
                        }
                        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                      />
                    </div>
                  )
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Category</label>
                  <select
                    value={form.category_id ?? ""}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        category_id: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                  >
                    <option value="">None</option>
                    {categories.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} {c.base_price > 0 ? `(₹${c.base_price})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Base price</label>
                  {categoryGoverns(form.category_id) ? (
                    <>
                      <input
                        type="number"
                        value={resolvedBasePrice(form.category_id)}
                        readOnly
                        disabled
                        className="w-full bg-gray-900 border border-gray-800 text-gray-400 rounded-lg px-3 py-2 text-sm cursor-not-allowed"
                      />
                      <p className="text-[11px] text-gray-500 mt-1">
                        Set by the category. Change it on the Categories tab, or
                        pick &ldquo;None&rdquo; to price this player individually.
                      </p>
                    </>
                  ) : (
                    <input
                      type="number"
                      value={form.base_price}
                      onChange={(e) => setForm({ ...form, base_price: e.target.value })}
                      placeholder={String(resolvedBasePrice(form.category_id))}
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                    />
                  )}
                </div>
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => {
                  setAddOpen(false);
                  setEditingId(null);
                  setForm(emptyForm);
                  setPhotoFile(null);
                  setPhotoPreview(null);
                }}
                className="flex-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 py-2 rounded-lg text-sm"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={saving || uploadingPhoto || !form.name.trim()}
                className="flex-1 bg-amber-500 hover:bg-amber-400 text-black font-semibold py-2 rounded-lg text-sm disabled:opacity-40"
              >
                {saving ? "Saving…" : editingId ? "Save changes" : "Add player"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CSV import modal */}
      {importOpen && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 w-full max-w-lg">
            <h3 className="text-lg font-bold mb-1">Import players from CSV</h3>
            <p className="text-xs text-gray-500 mb-4">
              The only required column is <code className="text-gray-300">name</code>.
              Optional: <code className="text-gray-300">email, phone, age,
              batting_rating, bowling_rating, fielding_rating, category, base_price</code>.
            </p>

            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setCsvFile(e.target.files?.[0] ?? null)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
            />

            {importReport && (
              <div className="mt-4 text-sm space-y-2">
                <p className="text-green-400">Imported {importReport.imported}</p>
                {importReport.skipped > 0 && (
                  <p className="text-amber-400">Skipped {importReport.skipped}</p>
                )}
                {importReport.errors.length > 0 && (
                  <ul className="text-xs text-gray-400 max-h-40 overflow-y-auto space-y-1">
                    {importReport.errors.map((err, i) => (
                      <li key={i}>• {err}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => {
                  setImportOpen(false);
                  setCsvFile(null);
                  setImportReport(null);
                }}
                className="flex-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 py-2 rounded-lg text-sm"
              >
                Close
              </button>
              <button
                onClick={runImport}
                disabled={!csvFile || importing}
                className="flex-1 bg-amber-500 hover:bg-amber-400 text-black font-semibold py-2 rounded-lg text-sm disabled:opacity-40"
              >
                {importing ? "Importing…" : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function OrganizerEventDetailPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const router = useRouter();
  const eid = parseInt(eventId);

  const [event, setEvent] = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rosterPlayers, setRosterPlayers] = useState<EventPlayer[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [bidSlabs, setBidSlabs] = useState<BidSlab[]>([]);
  const [categories, setCategories] = useState<PlayerCategory[]>([]);
  const [tab, setTab] = useState<"players" | "teams" | "auctioneer" | "settings" | "bid-slabs" | "categories">("players");
  const [teamForm, setTeamForm] = useState<{ name: string; color: string; logoFile?: File | null }>({
    name: "",
    color: "#3B82F6",
    logoFile: null,
  });
  const [settingsForm, setSettingsForm] = useState({
    budget: "100000",
    max_players: "15",
    min_players: "0",
    base_price: "100",
    no_timer: false,
    timer_seconds: "30",
    soft_close_threshold_seconds: "10",
    soft_close_extension_seconds: "10",
    auction_mode: "random",
    rtm_enabled: false,
    rtm_cards_per_team: "0",
    exclude_captains_from_auction: true,
    auto_reauction_unsold: false,
  });
  const [slabForm, setSlabForm] = useState({ min_value: 0, max_value: 0, increment: 1000, sort_order: 0 });
  const [catForm, setCatForm] = useState({ name: "", base_price: 1000, sort_order: 0, auction_first: false });
  const [toast, setToast] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [markingReady, setMarkingReady] = useState(false);
  const [scheduleInput, setScheduleInput] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [uploadingBg, setUploadingBg] = useState(false);
  const [creatingSlab, setCreatingSlab] = useState(false);
  const [creatingCat, setCreatingCat] = useState(false);

  const showToast = (type: "ok" | "err", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 3500);
  };

  const fetchAll = useCallback(async () => {
    const [evRes, aPlayers, teamsRes, readinessRes, slabsRes, catsRes] = await Promise.all([
      api.get(`/auction/events/${eid}`),
      api.get(`/organizer/events/${eid}/players`),
      api.get(`/organizer/events/${eid}/teams`),
      api.get(`/events/${eid}/readiness`),
      api.get(`/organizer/events/${eid}/bid-slabs`),
      api.get(`/organizer/events/${eid}/categories`),
    ]);
    if (evRes) {
      setEvent(evRes.data);
      setLoadError(null);
      setScheduleInput(toLocalDatetimeValue(evRes.data.scheduled_at));
      setSettingsForm({
        budget: evRes.data.team_budget?.toString() || "100000",
        max_players: evRes.data.team_max_players?.toString() || "15",
        min_players: (evRes.data.team_min_players ?? 0).toString(),
        base_price: evRes.data.player_base_price?.toString() || "100",
        no_timer: evRes.data.no_timer ?? false,
        timer_seconds: (evRes.data.timer_seconds ?? 30).toString(),
        soft_close_threshold_seconds: (
          evRes.data.soft_close_threshold_seconds ?? 10
        ).toString(),
        soft_close_extension_seconds: (
          evRes.data.soft_close_extension_seconds ?? 10
        ).toString(),
        auction_mode: evRes.data.auction_mode ?? "random",
        rtm_enabled: evRes.data.rtm_enabled ?? false,
        rtm_cards_per_team: (evRes.data.rtm_cards_per_team ?? 0).toString(),
        exclude_captains_from_auction: evRes.data.exclude_captains_from_auction ?? true,
        auto_reauction_unsold: evRes.data.auto_reauction_unsold ?? false,
      });
    }
    setRosterPlayers(aPlayers.data);
    setTeams(teamsRes.data);
    setReadiness(readinessRes.data);
    setBidSlabs(slabsRes.data);
    setCategories(catsRes.data);
  }, [eid]);

  useEffect(() => {
    setLoading(true);
    fetchAll()
      .catch((err) => {
        if (err.response?.status === 401) {
          router.push("/auth/login");
          return;
        }
        setLoadError(
          err.response?.data?.detail ||
            "Could not load this event. Check the connection and reload."
        );
      })
      .finally(() => setLoading(false));
  }, [eid, fetchAll, router]);

  const uploadTeamLogo = async (teamId: number, file: File) => {
    try {
      const formData = new FormData();
      formData.append("file", file);
      await api.post(`/organizer/events/${eid}/teams/${teamId}/logo`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      await fetchAll();
      showToast("ok", "Team logo uploaded");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to upload team logo");
    }
  };

  const deleteTeamLogo = async (teamId: number) => {
    try {
      await api.delete(`/organizer/events/${eid}/teams/${teamId}/logo`);
      await fetchAll();
      showToast("ok", "Team logo removed");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to remove team logo");
    }
  };

  const createTeam = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const { data: created } = await api.post(`/organizer/events/${eid}/teams`, {
        name: teamForm.name,
        color: teamForm.color,
      });
      if (teamForm.logoFile && created?.id) {
        await uploadTeamLogo(created.id, teamForm.logoFile);
      }
      setTeamForm({ name: "", color: "#3B82F6", logoFile: null });
      await fetchAll();
      showToast("ok", "Team created");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed");
    }
  };

  const assignCaptain = async (teamId: number, captainId: string) => {
    try {
      await api.patch(`/organizer/events/${eid}/teams/${teamId}`, {
        captain_id: captainId ? parseInt(captainId) : null,
      });
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed");
    }
  };

  const updateTeam = async (teamId: number, data: { name?: string; color?: string }) => {
    try {
      await api.patch(`/organizer/events/${eid}/teams/${teamId}`, data);
      await fetchAll();
      showToast("ok", "Team updated");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to update team");
    }
  };

  const deleteTeam = async (teamId: number) => {
    try {
      await api.delete(`/organizer/events/${eid}/teams/${teamId}`);
      await fetchAll();
      showToast("ok", "Team deleted");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to delete team");
    }
  };

  const markReady = async () => {
    setMarkingReady(true);
    try {
      await api.patch(`/organizer/events/${eid}/ready`);
      await fetchAll();
      showToast("ok", "Event is now ready for auction!");
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: { errors?: string[] } | string } } })
        ?.response?.data?.detail;
      if (detail && typeof detail === "object" && detail.errors) {
        showToast("err", detail.errors.join(" · "));
      } else {
        showToast("err", typeof detail === "string" ? detail : "Not ready yet");
      }
    } finally {
      setMarkingReady(false);
    }
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      const payload = {
        scheduled_at: scheduleInput ? new Date(scheduleInput).toISOString() : null,
        team_budget: parseInt(settingsForm.budget),
        team_max_players: parseInt(settingsForm.max_players),
        team_min_players: parseInt(settingsForm.min_players) || 0,
        player_base_price: parseInt(settingsForm.base_price),
        no_timer: false,
        timer_seconds: parseInt(settingsForm.timer_seconds) || 30,
        soft_close_threshold_seconds:
          parseInt(settingsForm.soft_close_threshold_seconds) || 0,
        soft_close_extension_seconds:
          parseInt(settingsForm.soft_close_extension_seconds) || 10,
        auction_mode: settingsForm.auction_mode,
        rtm_enabled: settingsForm.rtm_enabled,
        rtm_cards_per_team: parseInt(settingsForm.rtm_cards_per_team) || 0,
        exclude_captains_from_auction: settingsForm.exclude_captains_from_auction,
        auto_reauction_unsold: settingsForm.auto_reauction_unsold,
      };
      const { data } = await api.patch(`/organizer/events/${eid}/settings`, payload);
      setEvent(data);
      // Keep the saved checkbox state in the form. A full refetch here can
      // overwrite it with a stale event response and make the tick disappear.
      setSettingsForm((current) => ({
        ...current,
        auto_reauction_unsold:
          typeof data.auto_reauction_unsold === "boolean"
            ? data.auto_reauction_unsold
            : payload.auto_reauction_unsold,
      }));
      showToast("ok", "Settings saved successfully");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to update settings");
    } finally {
      setSavingSettings(false);
    }
  };

  const unpublish = async () => {
    try {
      const { data } = await api.patch(`/organizer/events/${eid}/unpublish`, {});
      setEvent(data);
      showToast("ok", "Event moved back to draft.");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to unpublish");
    }
  };

  const uploadLogo = async (file: File) => {
    setUploadingLogo(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post(`/organizer/events/${eid}/logo`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setEvent(data);
      showToast("ok", "Event logo uploaded successfully");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to upload logo");
    } finally {
      setUploadingLogo(false);
    }
  };

  const deleteLogo = async () => {
    try {
      const { data } = await api.delete(`/organizer/events/${eid}/logo`);
      setEvent(data);
      showToast("ok", "Event logo removed");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to remove logo");
    }
  };

  const uploadBackground = async (file: File) => {
    setUploadingBg(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post(`/organizer/events/${eid}/background`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setEvent(data);
      showToast("ok", "Background image uploaded successfully");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to upload background image");
    } finally {
      setUploadingBg(false);
    }
  };

  const deleteBackground = async () => {
    try {
      const { data } = await api.delete(`/organizer/events/${eid}/background`);
      setEvent(data);
      showToast("ok", "Background image removed");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to remove background image");
    }
  };

  // ── Bid Slabs ──
  const createBidSlab = async () => {
    setCreatingSlab(true);
    try {
      await api.post(`/organizer/events/${eid}/bid-slabs`, {
        min_value: slabForm.min_value,
        max_value: slabForm.max_value,
        increment: slabForm.increment,
        sort_order: slabForm.sort_order,
      });
      showToast("ok", "Bid slab created");
      setSlabForm({ min_value: 0, max_value: 0, increment: 1000, sort_order: 0 });
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to create bid slab");
    } finally {
      setCreatingSlab(false);
    }
  };

  const deleteBidSlab = async (id: number) => {
    try {
      await api.delete(`/organizer/events/${eid}/bid-slabs/${id}`);
      showToast("ok", "Bid slab deleted");
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to delete bid slab");
    }
  };

  // ── Player Categories ──
  const createCategory = async () => {
    setCreatingCat(true);
    try {
      await api.post(`/organizer/events/${eid}/categories`, {
        name: catForm.name,
        base_price: catForm.base_price,
        sort_order: catForm.sort_order,
        auction_first: catForm.auction_first,
      });
      showToast("ok", "Category created");
      setCatForm({ name: "", base_price: 1000, sort_order: 0, auction_first: false });
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to create category");
    } finally {
      setCreatingCat(false);
    }
  };

  const deleteCategory = async (id: number) => {
    try {
      await api.delete(`/organizer/events/${eid}/categories/${id}`);
      showToast("ok", "Category deleted");
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to delete category");
    }
  };

  const now = new Date();
  const isDraft = isDraftState(event);
  const publishState = derivePublishState(event, { loading, now });

  const TABS = [
    { key: "players", label: "Players", count: rosterPlayers.length },
    { key: "teams", label: "Teams", count: teams.length },
    { key: "auctioneer", label: "Auctioneer", count: readiness?.auctioneer_id ? 1 : 0 },
    { key: "bid-slabs", label: "Bid Slabs", count: bidSlabs.length },
    { key: "categories", label: "Categories", count: categories.length },
    { key: "settings", label: "Settings", count: 0 },
  ] as const;

  return (
    <div className="min-h-screen p-6 bg-gray-950">
      <div className="max-w-7xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <a href="/organizer/events" className="text-gray-500 hover:text-white text-sm">← Events</a>
            <h1 className="text-2xl font-bold">{event?.name || "Event Setup"}</h1>
            {event && (
              <span className={`text-xs px-2.5 py-1 rounded-full font-medium capitalize ${
                event.status === "ready" ? "bg-blue-500/20 text-blue-400" :
                event.status === "active" ? "bg-green-500/20 text-green-400" :
                "bg-gray-700 text-gray-400"
              }`}>{event.status}</span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Auction date</p>
              <p className="text-xs text-gray-300">
                {event?.scheduled_at
                  ? new Date(event.scheduled_at).toLocaleString("en-IN", {
                      weekday: "short",
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })
                  : "Not scheduled"}
              </p>
            </div>

            {publishState === "loading" && (
              <span className="text-gray-500 text-sm">Loading…</span>
            )}
            {event?.status === "draft" && (
              <div className="flex items-center gap-3">
                <span className="text-gray-400 text-sm font-medium">Draft</span>
                <button
                  className="btn-primary"
                  onClick={markReady}
                  disabled={markingReady || !readiness?.ready}
                >
                  {markingReady ? "Checking..." : "Mark Ready"}
                </button>
              </div>
            )}
            {event?.status === "ready" && (
              <>
                <span className="text-amber-400 text-sm font-medium">✓ Published (waiting for start)</span>
                <button
                  type="button"
                  className="btn-secondary text-xs ml-2"
                  onClick={unpublish}
                >
                  Unpublish
                </button>
              </>
            )}
            {(event?.status === "active" || event?.status === "paused") && (
              <span className="text-green-400 text-sm font-medium">✓ Event is live</span>
            )}
            {event?.status === "completed" && (
              <span className="text-blue-400 text-sm font-medium">✓ Auction complete</span>
            )}
            {publishState === "unknown" && (
              <span className="text-gray-500 text-sm">Unknown status</span>
            )}
          </div>
        </div>

        {loadError && (
          <div className="rounded-lg p-3 mb-4 text-sm bg-red-500/20 text-red-300 flex items-center justify-between gap-4">
            <span>{loadError}</span>
            <button
              onClick={() => {
                setLoading(true);
                setLoadError(null);
                fetchAll()
                  .catch(() => setLoadError("Still cannot load this event."))
                  .finally(() => setLoading(false));
              }}
              className="text-xs underline shrink-0"
            >
              Retry
            </button>
          </div>
        )}

        {/* Toast */}
        {toast && (
          <div className={`rounded-lg p-3 mb-4 text-sm ${
            toast.type === "ok"
              ? "bg-green-500/20 text-green-400"
              : "bg-red-500/20 text-red-400"
          }`}>
            {toast.text}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">

          {/* Readiness sidebar */}
          <div className="lg:col-span-1">
            <div className="card sticky top-6">
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Readiness Checklist
              </h3>
              <div className="space-y-2">
                {readiness && Object.entries(readiness.checks).map(([key, done]) => (
                  <div key={key} className="flex items-start gap-2">
                    <span className={`mt-0.5 shrink-0 ${done ? "text-green-400" : "text-gray-600"}`}>
                      {done ? "✓" : "○"}
                    </span>
                    <span className={`text-xs ${done ? "text-gray-300" : "text-gray-500"}`}>
                      {CHECK_LABELS[key] ?? key}
                    </span>
                  </div>
                ))}
              </div>
              {readiness?.ready && (
                <p className="text-green-400 text-xs mt-3 font-medium">
                  All checks passed!
                </p>
              )}
            </div>
          </div>

          {/* Main content */}
          <div className="lg:col-span-3">
            {/* Tabs */}
            <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit mb-5">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    tab === t.key ? "bg-amber-500 text-black" : "text-gray-400 hover:text-white"
                  }`}
                  onClick={() => setTab(t.key)}
                >
                  {t.label}
                  {t.count > 0 && (
                    <span className={`ml-1.5 text-xs px-1.5 py-0.5 rounded-full ${
                      tab === t.key ? "bg-black/20" : "bg-gray-700"
                    }`}>{t.count}</span>
                  )}
                </button>
              ))}
            </div>

            {/* Players tab */}
            {tab === "players" && (
              <PlayersTab
                eventId={eid}
                players={rosterPlayers}
                onChanged={fetchAll}
                onToast={showToast}
                editable={isDraft}
                eventStatus={event?.status}
                categories={categories}
                teams={teams}
                eventBasePrice={event?.player_base_price ?? 1000}
              />
            )}

            {/* Teams tab */}
            {tab === "teams" && (
              <TeamsTab
                eventId={eid}
                teams={teams}
                rosterPlayers={rosterPlayers}
                teamForm={teamForm}
                setTeamForm={setTeamForm}
                onCreateTeam={createTeam}
                onAssignCaptain={assignCaptain}
                onUpdateTeam={updateTeam}
                onDeleteTeam={deleteTeam}
                onUploadTeamLogo={uploadTeamLogo}
                onDeleteTeamLogo={deleteTeamLogo}
                editable={isDraft}
              />
            )}

            {/* Auctioneer tab */}
            {tab === "auctioneer" && (
              <div className="card">
                <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-1">
                  Assign Auctioneer
                </h3>
                <p className="text-xs text-gray-600 mb-4">
                  Search by name or email. If the person isn&apos;t registered yet, they&apos;ll receive an invite link.
                </p>
                <div className={isDraft ? "" : "pointer-events-none opacity-75"}>
                  <UserSearchInvite
                    eventId={eid}
                    role="auctioneer"
                    label="Auctioneer"
                    currentUserId={readiness?.auctioneer_id}
                    inviteEndpoint={`/organizer/events/${eid}/invite-auctioneer`}
                    searchEndpoint="/organizer/users/search"
                    userEndpoint="/organizer/users"
                    onAssigned={async () => { await fetchAll(); showToast("ok", "Auctioneer assigned!"); }}
                  />
                </div>
                {publishState !== "draft" && publishState !== "loading" && (
                  <p className="text-[11px] text-gray-500 mt-2">
                    Unpublish the event to change the auctioneer.
                  </p>
                )}
              </div>
            )}

            {/* Settings tab */}
            {tab === "settings" && (
              <div className="card">
                <div className="space-y-5">
                  <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
                    Event Settings
                  </h3>

                  <div className="space-y-6">
                    {/* Event Logo & Background Image */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                      {/* Event Logo */}
                      <div>
                        <label className="label text-gray-300 mb-1 block">Event Logo</label>
                        <p className="text-xs text-gray-500 mb-3">
                          Displayed on event cards and the auction header.
                        </p>
                        <div className="flex items-center gap-4">
                          {event?.logo ? (
                            <div className="relative group">
                              <div className="w-20 h-20 rounded-xl overflow-hidden bg-gray-800 border border-gray-700">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img src={event.logo} alt="Event logo" className="w-full h-full object-cover" />
                              </div>
                              <button
                                type="button"
                                onClick={deleteLogo}
                                className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 hover:bg-red-600 rounded-full text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                                title="Remove logo"
                              >
                                ×
                              </button>
                            </div>
                          ) : (
                            <label className="w-20 h-20 rounded-xl border-2 border-dashed border-gray-700 hover:border-amber-500/50 hover:bg-amber-500/5 cursor-pointer flex flex-col items-center justify-center text-gray-500 hover:text-amber-400 transition-all">
                              {uploadingLogo ? (
                                <span className="text-xs">Uploading...</span>
                              ) : (
                                <>
                                  <span className="text-2xl mb-1">📷</span>
                                  <span className="text-[10px]">Add Logo</span>
                                </>
                              )}
                              <input
                                type="file"
                                accept="image/*"
                                className="hidden"
                                disabled={uploadingLogo}
                                onChange={(e) => {
                                  const file = e.target.files?.[0];
                                  if (file) uploadLogo(file);
                                }}
                              />
                            </label>
                          )}
                          {event?.logo && (
                            <label className="btn-secondary text-xs cursor-pointer">
                              {uploadingLogo ? "Uploading..." : "Change Logo"}
                              <input
                                type="file"
                                accept="image/*"
                                className="hidden"
                                disabled={uploadingLogo}
                                onChange={(e) => {
                                  const file = e.target.files?.[0];
                                  if (file) uploadLogo(file);
                                }}
                              />
                            </label>
                          )}
                        </div>
                      </div>

                      {/* Event Background Image */}
                      <div>
                        <label className="label text-gray-300 mb-1 block">Background Image</label>
                        <p className="text-xs text-gray-500 mb-3">
                          Displayed behind the auction stage and spectator screen.
                        </p>
                        <div className="flex items-center gap-4">
                          {event?.background_image ? (
                            <div className="relative group">
                              <div className="w-32 h-20 rounded-xl overflow-hidden bg-gray-800 border border-gray-700">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                  src={event.background_image}
                                  alt="Event background"
                                  className="w-full h-full object-cover"
                                />
                              </div>
                              <button
                                type="button"
                                onClick={deleteBackground}
                                className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 hover:bg-red-600 rounded-full text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                                title="Remove background image"
                              >
                                ×
                              </button>
                            </div>
                          ) : (
                            <label className="w-32 h-20 rounded-xl border-2 border-dashed border-gray-700 hover:border-amber-500/50 hover:bg-amber-500/5 cursor-pointer flex flex-col items-center justify-center text-gray-500 hover:text-amber-400 transition-all">
                              {uploadingBg ? (
                                <span className="text-xs">Uploading...</span>
                              ) : (
                                <>
                                  <span className="text-2xl mb-1">🖼️</span>
                                  <span className="text-[10px]">Add Background</span>
                                </>
                              )}
                              <input
                                type="file"
                                accept="image/*"
                                className="hidden"
                                disabled={uploadingBg}
                                onChange={(e) => {
                                  const file = e.target.files?.[0];
                                  if (file) uploadBackground(file);
                                }}
                              />
                            </label>
                          )}
                          {event?.background_image && (
                            <label className="btn-secondary text-xs cursor-pointer">
                              {uploadingBg ? "Uploading..." : "Change Background"}
                              <input
                                type="file"
                                accept="image/*"
                                className="hidden"
                                disabled={uploadingBg}
                                onChange={(e) => {
                                  const file = e.target.files?.[0];
                                  if (file) uploadBackground(file);
                                }}
                              />
                            </label>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="h-px bg-gray-800 w-full"></div>

                    {/* Schedule */}
                    <div>
                      <label className="label text-gray-300 mb-1 block">Auction Schedule</label>
                      <p className="text-xs text-gray-500 mb-3">
                        Set or adjust the auction date and time. This will be visible to all participants.
                      </p>
                      <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
                        <input
                          type="datetime-local"
                          className={`input max-w-sm ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                          value={scheduleInput}
                          onChange={(e) => setScheduleInput(e.target.value)}
                          disabled={!isDraft}
                        />
                      </div>
                    </div>

                    <div className="h-px bg-gray-800 w-full"></div>

                    {/* Budget & Max Players */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                      <div>
                        <label className="label text-gray-300 mb-1 block">Team Budget (credits)</label>
                        <p className="text-xs text-gray-500 mb-3">
                          Total amount each team has to spend.
                        </p>
                        <input
                          className={`input max-w-sm ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                          type="number"
                          min={100}
                          value={settingsForm.budget}
                          onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, budget: e.target.value })}
                          disabled={!isDraft}
                        />
                      </div>
                      <div>
                        <label className="label text-gray-300 mb-1 block">Max Players per Team</label>
                        <p className="text-xs text-gray-500 mb-3">
                          Maximum number of players a team can buy.
                        </p>
                        <input
                          className={`input max-w-sm ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                          type="number"
                          min={1}
                          max={25}
                          value={settingsForm.max_players}
                          onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, max_players: e.target.value })}
                          disabled={!isDraft}
                        />
                      </div>
                      <div>
                        <label className="label text-gray-300 mb-1 block">Player Base Price</label>
                        <p className="text-xs text-gray-500 mb-3">
                          Starting bid amount for all players.
                        </p>
                        <input
                          className={`input max-w-sm ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                          type="number"
                          min={1}
                          value={settingsForm.base_price}
                          onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, base_price: e.target.value })}
                          disabled={!isDraft}
                        />
                      </div>
                      <div>
                        <label className="label text-gray-300 mb-1 block">Minimum Squad Size</label>
                        <p className="text-xs text-gray-500 mb-3">
                          Budget is held back so a team that spends heavily early can
                          still fill this many players. 0 disables the reserve.
                        </p>
                        <input
                          className={`input max-w-sm ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                          type="number"
                          min={0}
                          max={parseInt(settingsForm.max_players) || 25}
                          value={settingsForm.min_players}
                          onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, min_players: e.target.value })}
                          disabled={!isDraft}
                        />
                      </div>
                    </div>

                    <div className="h-px bg-gray-800 w-full"></div>

                    {/* Timing */}
                    <div>
                      <h4 className="text-sm font-semibold text-gray-200 mb-1">Clock</h4>
                      <p className="text-xs text-gray-500 mb-4">
                        A bid landing inside the soft-close window extends the clock
                        rather than resetting it, and each successive extension is
                        shorter — so a two-team bidding war still ends. These can be
                        tuned while the auction is running.
                      </p>

                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                          <div>
                            <label className="label text-gray-300 mb-1 block text-xs">
                              Base countdown (s)
                            </label>
                            <input
                              className="input"
                              type="number"
                              min={5}
                              max={600}
                              value={settingsForm.timer_seconds}
                              onChange={(e) => setSettingsForm({ ...settingsForm, timer_seconds: e.target.value })}
                            />
                          </div>
                          <div>
                            <label className="label text-gray-300 mb-1 block text-xs">
                              Extend if under (s)
                            </label>
                            <input
                              className="input"
                              type="number"
                              min={0}
                              max={120}
                              value={settingsForm.soft_close_threshold_seconds}
                              onChange={(e) => setSettingsForm({ ...settingsForm, soft_close_threshold_seconds: e.target.value })}
                            />
                          </div>
                          <div>
                            <label className="label text-gray-300 mb-1 block text-xs">
                              Extend by (s)
                            </label>
                            <input
                              className="input"
                              type="number"
                              min={1}
                              max={120}
                              value={settingsForm.soft_close_extension_seconds}
                              onChange={(e) => setSettingsForm({ ...settingsForm, soft_close_extension_seconds: e.target.value })}
                            />
                          </div>
                        </div>
                    </div>

                    <div className="h-px bg-gray-800 w-full"></div>

                    {/* Order of play */}
                    <div>
                      <h4 className="text-sm font-semibold text-gray-200 mb-1">Order of Play</h4>
                      <p className="text-xs text-gray-500 mb-3">
                        Random draws from the pending pool, honouring any
                        &ldquo;auction first&rdquo; categories. Nomination is the
                        accelerated round: teams pick who comes up next.
                      </p>
                      <select
                        className={`input max-w-sm ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                        value={settingsForm.auction_mode}
                        onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, auction_mode: e.target.value })}
                        disabled={!isDraft}
                      >
                        <option value="random">Random draw</option>
                        <option value="nomination">Teams nominate (accelerated round)</option>
                      </select>
                    </div>

                    <div className="h-px bg-gray-800 w-full"></div>

                    {/* Auction pool rules */}
                    <div>
                      <h4 className="text-sm font-semibold text-gray-200 mb-1">Auction Pool &amp; Re-auction</h4>
                      <p className="text-xs text-gray-500 mb-3">Control which players enter the auction and whether unsold players receive one final round.</p>
                      <div className="space-y-3">
                        <label className="flex items-start gap-2 text-xs text-gray-300">
                          <input type="checkbox" checked={settingsForm.exclude_captains_from_auction} onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, exclude_captains_from_auction: e.target.checked })} disabled={!isDraft} />
                          <span><strong>Exclude assigned team captains</strong><span className="block text-gray-500">Players selected as team captains will not appear in the auction pool.</span></span>
                        </label>
                        <label className="flex items-start gap-2 text-xs text-gray-300">
                          <input type="checkbox" checked={settingsForm.auto_reauction_unsold} onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, auto_reauction_unsold: e.target.checked })} disabled={!isDraft} />
                          <span><strong>Automatically re-auction NO BID / unsold players</strong><span className="block text-gray-500">After all other eligible players have been processed, run one additional pass of unsold players.</span></span>
                        </label>
                      </div>
                    </div>

                    <div className="h-px bg-gray-800 w-full"></div>

                    {/* Right to Match */}
                    <div>
                      <h4 className="text-sm font-semibold text-gray-200 mb-1">Right to Match</h4>
                      <p className="text-xs text-gray-500 mb-3">
                        Lets the team holding rights on a player reclaim them by
                        matching the winning bid. Assign rights per player on the
                        Players tab. Leave this off for a first season — there are no
                        prior rights to match.
                      </p>

                      <div className="flex items-center gap-2 mb-4">
                        <input
                          type="checkbox"
                          checked={settingsForm.rtm_enabled}
                          onChange={(e) =>
                            isDraft && setSettingsForm({ ...settingsForm, rtm_enabled: e.target.checked })
                          }
                          disabled={!isDraft}
                        />
                        <span className="text-xs text-gray-300">Enable Right to Match</span>
                      </div>

                      {settingsForm.rtm_enabled && (
                        <div>
                          <label className="label text-gray-300 mb-1 block text-xs">
                            Cards per team
                          </label>
                          <p className="text-xs text-gray-500 mb-2">
                            How many times a team may use it. 0 means unlimited.
                          </p>
                          <input
                            className={`input max-w-xs ${!isDraft ? "bg-gray-900 text-gray-500 cursor-not-allowed" : ""}`}
                            type="number"
                            min={0}
                            max={20}
                            value={settingsForm.rtm_cards_per_team}
                            onChange={(e) => isDraft && setSettingsForm({ ...settingsForm, rtm_cards_per_team: e.target.value })}
                            disabled={!isDraft}
                          />
                        </div>
                      )}
                    </div>

                    <div className="pt-2">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={saveSettings}
                        disabled={savingSettings}
                      >
                        {savingSettings ? "Saving..." : "Save Settings"}
                      </button>
                      {!isDraft && (
                        <p className="text-[11px] text-gray-500 mt-2">
                          Money and squad rules are locked once the event is published.
                          Clock settings can still be changed.
                        </p>
                      )}
                    </div>
                  </div>

                  {publishState !== "draft" && publishState !== "loading" && (
                    <p className="text-[11px] text-gray-500 mt-4 pt-4 border-t border-gray-800">
                      Unpublish the event to adjust settings.
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Bid Slabs tab */}
            {tab === "bid-slabs" && (
              <div className="card">
                <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
                  Bid Slabs
                </h3>
                <p className="text-xs text-gray-500 mb-4">
                  Define minimum bid increments based on the current bid value. For example:
                  10,000–100,000 → increment ₹2,000; 100,000–500,000 → increment ₹7,000.
                </p>

                {isDraft ? (
                  <div className="space-y-3 mb-4">
                    <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                      <input
                        type="number"
                        placeholder="Min value"
                        className="input text-sm"
                        value={slabForm.min_value}
                        onChange={(e) => setSlabForm({ ...slabForm, min_value: Number(e.target.value) || 0 })}
                      />
                      <input
                        type="number"
                        placeholder="Max value (0 = no limit)"
                        className="input text-sm"
                        value={slabForm.max_value}
                        onChange={(e) => setSlabForm({ ...slabForm, max_value: Number(e.target.value) || 0 })}
                      />
                      <input
                        type="number"
                        placeholder="Increment"
                        className="input text-sm"
                        value={slabForm.increment}
                        onChange={(e) => setSlabForm({ ...slabForm, increment: Number(e.target.value) || 1000 })}
                      />
                      <input
                        type="number"
                        placeholder="Sort order"
                        className="input text-sm"
                        value={slabForm.sort_order}
                        onChange={(e) => setSlabForm({ ...slabForm, sort_order: Number(e.target.value) || 0 })}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn-primary w-full"
                      onClick={createBidSlab}
                      disabled={creatingSlab}
                    >
                      {creatingSlab ? "Creating..." : "Add Bid Slab"}
                    </button>
                  </div>
                ) : (
                  <p className="text-[11px] text-gray-500">
                    Unpublish the event to edit bid slabs.
                  </p>
                )}

                <div className="mt-4">
                  {bidSlabs.length === 0 ? (
                    <p className="text-xs text-gray-500 text-center py-4">No bid slabs defined. Default increment applies.</p>
                  ) : (
                    <div className="space-y-2">
                      {bidSlabs.map((s) => (
                        <div key={s.id} className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-lg p-3">
                          <div>
                            <p className="text-sm font-medium text-white">
                              {s.min_value.toLocaleString()} – {s.max_value === 0 ? "∞" : s.max_value.toLocaleString()}
                            </p>
                            <p className="text-xs text-gray-500">Increment: ₹{s.increment.toLocaleString()} · Order: {s.sort_order}</p>
                          </div>
                          {isDraft && (
                            <button
                              type="button"
                              className="text-xs text-red-400 hover:text-red-300"
                              onClick={() => deleteBidSlab(s.id)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Categories tab */}
            {tab === "categories" && (
              <div className="card">
                <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
                  Player Categories
                </h3>
                <p className="text-xs text-gray-500 mb-4">
                  Create categories (e.g. Captain, High Ranking, Mid Ranking) with their own base price.
                  Categories marked &quot;Auction First&quot; will be auctioned before the random pool.
                </p>

                {isDraft ? (
                  <div className="space-y-3 mb-4">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <input
                        type="text"
                        placeholder="Category name"
                        className="input text-sm"
                        value={catForm.name}
                        onChange={(e) => setCatForm({ ...catForm, name: e.target.value })}
                      />
                      <input
                        type="number"
                        placeholder="Base price"
                        className="input text-sm"
                        value={catForm.base_price}
                        onChange={(e) => setCatForm({ ...catForm, base_price: Number(e.target.value) || 1000 })}
                      />
                    </div>
                    <div className="flex items-center gap-3">
                      <label className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={catForm.auction_first}
                          onChange={(e) => setCatForm({ ...catForm, auction_first: e.target.checked })}
                        />
                        Auction first
                      </label>
                      <input
                        type="number"
                        placeholder="Sort order"
                        className="input text-sm w-20"
                        value={catForm.sort_order}
                        onChange={(e) => setCatForm({ ...catForm, sort_order: Number(e.target.value) || 0 })}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn-primary w-full"
                      onClick={createCategory}
                      disabled={creatingCat}
                    >
                      {creatingCat ? "Creating..." : "Add Category"}
                    </button>
                  </div>
                ) : (
                  <p className="text-[11px] text-gray-500">
                    Unpublish the event to edit categories.
                  </p>
                )}

                <div className="mt-4">
                  {categories.length === 0 ? (
                    <p className="text-xs text-gray-500 text-center py-4">No categories defined.</p>
                  ) : (
                    <div className="space-y-2">
                      {categories.map((c) => (
                        <div key={c.id} className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-lg p-3">
                          <div>
                            <p className="text-sm font-medium text-white">{c.name}</p>
                            <p className="text-xs text-gray-500">
                              Base price: ₹{c.base_price.toLocaleString()} · Order: {c.sort_order}{c.auction_first ? " · Auction first" : ""}
                            </p>
                          </div>
                          {isDraft && (
                            <button
                              type="button"
                              className="text-xs text-red-400 hover:text-red-300"
                              onClick={() => deleteCategory(c.id)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

          </div>
        </div>
      </div>
    </div>
  );
}