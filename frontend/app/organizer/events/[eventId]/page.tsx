"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import api from "@/lib/api";
import UserSearchInvite from "@/components/UserSearchInvite";

interface User {
  id: number;
  name: string;
  email: string;
  roles: string[];
  phone: string | null;
  age: number | null;
  batting_rating: number;
  bowling_rating: number;
  fielding_rating: number;
  profile_photo: string | null;
}

interface AuctionPlayer {
  id: number;
  player_id: number;
  base_price: number;
  status: string;
  category_id: number | null;
}

interface Team {
  id: number;
  name: string;
  color: string;
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

// IPL-inspired preset palette
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
      {/* Preset swatches */}
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
        {/* Custom color swatch */}
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
      {/* Preview */}
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
  eligiblePlayers,
  teamForm,
  setTeamForm,
  onCreateTeam,
  onAssignCaptain,
  onUpdateTeam,
  onDeleteTeam,
  editable,
}: {
  eventId: number;
  teams: Team[];
  eligiblePlayers: User[];
  teamForm: { name: string; color: string; budget?: string; max_players?: string };
  setTeamForm: (f: { name: string; color: string; budget?: string; max_players?: string }) => void;
  onCreateTeam: (e: React.FormEvent) => void;
  onAssignCaptain: (teamId: number, captainId: string) => void;
  onUpdateTeam: (teamId: number, data: { name?: string; color?: string }) => void;
  onDeleteTeam: (teamId: number) => void;
  editable: boolean;
}) {
  const [showForm, setShowForm] = useState(editable && teams.length === 0);
  const [editingTeamId, setEditingTeamId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState({ name: "", color: "" });
  const playerMap = Object.fromEntries(eligiblePlayers.map((p) => [p.id, p]));
  
  // Get all captain IDs that are already assigned to other teams
  const assignedCaptainIds = new Set(
    teams.filter((t) => t.captain_id !== null).map((t) => t.captain_id as number)
  );

  const handleSubmit = async (e: React.FormEvent) => {
    await onCreateTeam(e);
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
            
            // Filter out captains assigned to OTHER teams (but keep current team's captain in the list)
            const availableCaptains = eligiblePlayers.filter(
              (p) => !assignedCaptainIds.has(p.id) || p.id === team.captain_id
            );

            return (
              <div key={team.id} className="bg-gray-900 border border-gray-800 rounded-2xl overflow-hidden">
                {/* Colored header */}
                <div
                  className="px-5 py-4 flex items-center justify-between"
                  style={{ backgroundColor: team.color }}
                >
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
                    <div>
                      <h3 className="font-bold text-lg" style={{ color: textColor }}>{team.name}</h3>
                      <p className="text-xs mt-0.5" style={{ color: subtextColor }}>
                        Budget: {team.budget.toLocaleString()} · Max {team.max_players} players
                      </p>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
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

            <div>
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

function isValidEmail(val: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val);
}

function toLocalDatetimeValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function PlayersTab({
  eventId,
  eligiblePlayers,
  auctionPlayers,
  onAdd,
  onRemove,
  onInvited,
  onToast,
  editable,
  eventStatus,
  categories,
  createProfileOpen,
  setCreateProfileOpen,
  profileForm,
  setProfileForm,
  creatingProfile,
  createPlayerProfile,
}: {
  eventId: number;
  eligiblePlayers: User[];
  auctionPlayers: AuctionPlayer[];
  onAdd: (id: number, categoryId?: number) => void;
  onRemove: (id: number) => void;
  onInvited: () => void;
  onToast: (type: "ok" | "err", text: string) => void;
  editable: boolean;
  eventStatus: string | undefined;
  categories: PlayerCategory[];
  createProfileOpen: boolean;
  setCreateProfileOpen: (v: boolean) => void;
  profileForm: {
    name: string;
    email: string;
    phone: string;
    age: string;
    batting_rating: number;
    bowling_rating: number;
    fielding_rating: number;
    password: string;
  };
  setProfileForm: (f: {
    name: string;
    email: string;
    phone: string;
    age: string;
    batting_rating: number;
    bowling_rating: number;
    fielding_rating: number;
    password: string;
  }) => void;
  creatingProfile: boolean;
  createPlayerProfile: () => Promise<void>;
}) {
  const [search, setSearch] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviting, setInviting] = useState(false);
  const [displayCount, setDisplayCount] = useState(15);
  const [filter, setFilter] = useState<"all" | "added" | "not_added">("all");
  const [importOpen, setImportOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [pendingAdd, setPendingAdd] = useState<number | null>(null); // player waiting for category selection
  const [selectedCategory, setSelectedCategory] = useState<number | null>(null);
  const [editPlayerId, setEditPlayerId] = useState<number | null>(null);
  const [editProfileOpen, setEditProfileOpen] = useState(false);
  const [editProfileForm, setEditProfileForm] = useState({
    name: "",
    email: "",
    phone: "",
    age: "",
    batting_rating: 5,
    bowling_rating: 5,
    fielding_rating: 5,
    category_id: null as number | null,
  });
  const [editingProfile, setEditingProfile] = useState(false);
  const addedIds = new Set(auctionPlayers.map((ap) => ap.player_id));

  // If the event is completed, only show the players who were actually added to the auction
  let basePlayers = eventStatus === "completed" 
    ? eligiblePlayers.filter((p) => addedIds.has(p.id))
    : eligiblePlayers;

  if (filter === "added") {
    basePlayers = basePlayers.filter((p) => addedIds.has(p.id));
  } else if (filter === "not_added") {
    basePlayers = basePlayers.filter((p) => !addedIds.has(p.id));
  }

  const filtered = basePlayers.filter(
    (p) =>
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.email.toLowerCase().includes(search.toLowerCase())
  );

  // Is the typed search text an email not already in the list?
  const notFound =
    search.length > 3 &&
    isValidEmail(search) &&
    !basePlayers.some((p) => p.email.toLowerCase() === search.toLowerCase());

  const sendInvite = async (email: string) => {
    setInviting(true);
    try {
      const { data } = await api.post(`/organizer/events/${eventId}/invite-player`, { email });
      onToast("ok", data.message);
      setSearch("");
      if (data.status === "added") onInvited();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      onToast("err", msg || "Failed to invite");
    } finally {
      setInviting(false);
    }
  };

  const handleImportCsv = async () => {
    if (!csvFile) return;
    setImporting(true);
    const formData = new FormData();
    formData.append("file", csvFile);
    try {
      const { data } = await api.post(`/organizer/events/${eventId}/import-players`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      onToast("ok", `Imported ${data.imported} players${data.skipped ? ` (${data.skipped} skipped)` : ""}`);
      if (data.errors.length) {
        console.warn("Import warnings:", data.errors);
      }
      setImportOpen(false);
      setCsvFile(null);
      onInvited();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      onToast("err", msg || "Import failed");
    } finally {
      setImporting(false);
    }
  };

  const handleExportXlsx = async () => {
    setExporting(true);
    try {
      const res = await api.get(`/organizer/events/${eventId}/players/export-xlsx`, {
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      const disposition = res.headers["content-disposition"] || "";
      const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
      a.download = filenameMatch ? filenameMatch[1] : `event_${eventId}_players.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      onToast("ok", "Players exported to Excel");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      onToast("err", msg || "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const openEditProfile = (player: User) => {
    const ap = auctionPlayers.find((a) => a.player_id === player.id);
    setEditProfileForm({
      name: player.name,
      email: player.email,
      phone: player.phone || "",
      age: player.age ? String(player.age) : "",
      batting_rating: player.batting_rating,
      bowling_rating: player.bowling_rating,
      fielding_rating: player.fielding_rating,
      category_id: ap?.category_id ?? null,
    });
    setEditPlayerId(player.id);
    setEditProfileOpen(true);
  };

  const editPlayerProfile = async () => {
    if (!editPlayerId) return;
    setEditingProfile(true);
    try {
      const payload: Record<string, unknown> = {};
      if (editProfileForm.name) payload.name = editProfileForm.name;
      if (editProfileForm.email) payload.email = editProfileForm.email;
      if (editProfileForm.phone) payload.phone = editProfileForm.phone;
      if (editProfileForm.age) payload.age = Number(editProfileForm.age);
      payload.batting_rating = editProfileForm.batting_rating;
      payload.bowling_rating = editProfileForm.bowling_rating;
      payload.fielding_rating = editProfileForm.fielding_rating;
      if (editProfileForm.category_id !== null && editProfileForm.category_id !== undefined) {
        payload.category_id = editProfileForm.category_id;
      }

      const { data } = await api.patch(
        `/organizer/events/${eventId}/players/${editPlayerId}/profile`,
        payload
      );
      onToast("ok", `Updated ${data.name}`);
      setEditProfileOpen(false);
      setEditPlayerId(null);
      onInvited();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      onToast("err", msg || "Failed to update profile");
    } finally {
      setEditingProfile(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Filters and Search */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 text-sm">🔍</div>
          <input
            className="input pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or email — or type a full email to invite"
          />
        </div>
        
        {eventStatus !== "completed" && (
          <>
            <select
              className="input w-full sm:w-40 text-sm bg-gray-900"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value as "all" | "added" | "not_added");
                setDisplayCount(15);
              }}
            >
              <option value="all">All Players</option>
              <option value="added">Added ({auctionPlayers.length})</option>
              <option value="not_added">Not Added ({eligiblePlayers.length - auctionPlayers.length})</option>
            </select>
            {editable && (
              <>
                <a
                  href="/sample-players.csv"
                  download
                  className="btn-secondary text-sm px-4 py-2 shrink-0"
                >
                  📄 Download Sample CSV
                </a>
                <button
                  type="button"
                  className="btn-primary text-sm px-4 py-2 shrink-0"
                  onClick={() => setImportOpen(true)}
                  disabled={importing}
                >
                  {importing ? "Importing..." : "Import CSV"}
                </button>
                <button
                  type="button"
                  className="btn-secondary text-sm px-4 py-2 shrink-0 border-green-500/30 hover:border-green-500 hover:bg-green-500/10"
                  disabled={auctionPlayers.length === 0 || exporting}
                  onClick={handleExportXlsx}
                >
                  {exporting ? "Exporting..." : "Export XLSX"}
                </button>
                <button
                  type="button"
                  className="btn-secondary text-sm px-4 py-2 shrink-0 border-amber-500/30 hover:border-amber-500 hover:bg-amber-500/10"
                  onClick={() => setCreateProfileOpen(true)}
                  disabled={creatingProfile}
                >
                  {creatingProfile ? "Creating..." : "+ Create Player Profile"}
                </button>
              </>
            )}
          </>
        )}
      </div>

      {/* Invite unregistered email prompt */}
      {editable && notFound && (
        <div className="flex items-center justify-between bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-3">
          <div>
            <p className="text-sm text-amber-300 font-medium">Not registered yet</p>
            <p className="text-xs text-gray-500 mt-0.5">{search}</p>
          </div>
          <button
            className="btn-primary text-sm py-1.5 px-4 shrink-0"
            disabled={inviting}
            onClick={() => sendInvite(search)}
          >
            {inviting ? "Sending..." : "Send Invite"}
          </button>
        </div>
      )}

      {/* Player grid */}
      {eligiblePlayers.length === 0 ? (
        <div className="card text-center py-10 text-gray-500 text-sm">
          No eligible players found. Make sure allowed email domains are configured in the admin panel.
        </div>
      ) : filtered.length === 0 && !notFound ? (
        <div className="text-center py-8 text-gray-600 text-sm">
          No players match &ldquo;{search}&rdquo;
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.slice(0, displayCount).map((player) => {
              const added = addedIds.has(player.id);
              return (
                <div
                  key={player.id}
                  className={`flex items-center gap-3 bg-gray-900 border rounded-xl p-3 transition-colors ${
                    added ? "border-green-800/50" : "border-gray-800"
                  }`}
                >
                  <div className="w-10 h-10 rounded-full bg-gray-800 overflow-hidden shrink-0 flex items-center justify-center">
                    {player.profile_photo ? (
                      <img src={player.profile_photo} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <span className="text-lg">👤</span>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm truncate">{player.name}</p>
                    <p className="text-xs text-gray-500 truncate">{player.email}</p>
                    <div className="flex gap-2 text-xs text-gray-600 mt-0.5">
                      <span>🏏 {player.batting_rating}</span>
                      <span>🎯 {player.bowling_rating}</span>
                      <span>🤸 {player.fielding_rating}</span>
                    </div>
                  </div>
                  {added ? (
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="badge-sold">Added ✓</span>
                      {editable && (
                        <>
                          <button
                            className="text-blue-400 hover:text-blue-300 transition-colors text-lg leading-none"
                            onClick={() => openEditProfile(player)}
                            title="Edit profile"
                          >
                            ✏️
                          </button>
                          <button
                            className="text-gray-600 hover:text-red-400 transition-colors text-lg leading-none"
                            onClick={() => onRemove(player.id)}
                            title="Remove from event"
                          >
                            ×
                          </button>
                        </>
                      )}
                    </div>
                  ) : editable && categories.length > 0 ? (
                    <button
                      className="btn-primary text-xs px-3 py-1.5 shrink-0"
                      onClick={() => { setPendingAdd(player.id); setSelectedCategory(null); }}
                    >
                      Add
                    </button>
                  ) : editable ? (
                    <button
                      className="btn-primary text-xs px-3 py-1.5 shrink-0"
                      onClick={() => onAdd(player.id)}
                    >
                      Add
                    </button>
                  ) : (
                    <span className="text-xs text-gray-600">Locked</span>
                  )}
                </div>
              );
            })}
          </div>
          {displayCount < filtered.length && (
            <div className="text-center mt-6">
              <button
                className="btn-secondary text-sm px-6 py-2"
                onClick={() => setDisplayCount(v => v + 15)}
              >
                Load More ({filtered.length - displayCount} remaining)
              </button>
            </div>
          )}

          {/* Category selection dialog for adding a player */}
          {pendingAdd && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
              <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 max-w-md w-full shadow-2xl">
                <h3 className="text-lg font-bold text-white mb-1">Assign Category</h3>
                <p className="text-sm text-gray-400 mb-4">
                  Select a category for this player to set their base price.
                </p>

                <div className="space-y-3 mb-6">
                  <label className="block text-xs font-medium text-gray-400 uppercase">Category</label>
                  <select
                    className="input w-full text-sm bg-gray-800 border-gray-700"
                    value={selectedCategory || ""}
                    onChange={(e) => setSelectedCategory(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">None (use event base price)</option>
                    {categories.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} — ₹{c.base_price.toLocaleString()}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex justify-end gap-3">
                  <button
                    type="button"
                    className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white transition-colors"
                    onClick={() => { setPendingAdd(null); setSelectedCategory(null); }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn-primary px-6 py-2"
                    disabled={selectedCategory === null}
                    onClick={() => {
                      if (pendingAdd) {
                        onAdd(pendingAdd, selectedCategory || undefined);
                        setPendingAdd(null);
                        setSelectedCategory(null);
                      }
                    }}
                  >
                    Assign & Add
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {/* Summary footer */}
      <p className="text-xs text-gray-600 text-right mt-4">
        {auctionPlayers.length} of {basePlayers.length} eligible players added
      </p>

      {/* Edit Player Profile dialog */}
      {editProfileOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 max-w-md w-full shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-1">Edit Player Profile</h3>
            <p className="text-sm text-gray-400 mb-6">
              Update the player's details. Email changes must be unique.
            </p>

            <form onSubmit={(e) => { e.preventDefault(); editPlayerProfile(); }} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Full Name</label>
                <input
                  type="text"
                  className="input w-full text-sm"
                  value={editProfileForm.name}
                  onChange={(e) => setEditProfileForm({ ...editProfileForm, name: e.target.value })}
                  placeholder="Virat Kohli"
                  required
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Email</label>
                <input
                  type="email"
                  className="input w-full text-sm"
                  value={editProfileForm.email}
                  onChange={(e) => setEditProfileForm({ ...editProfileForm, email: e.target.value })}
                  placeholder="virat@example.com"
                  required
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Phone</label>
                <input
                  type="tel"
                  className="input w-full text-sm"
                  value={editProfileForm.phone}
                  onChange={(e) => setEditProfileForm({ ...editProfileForm, phone: e.target.value })}
                  placeholder="+91 98765 43210"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Age</label>
                <input
                  type="number"
                  className="input w-full text-sm"
                  value={editProfileForm.age}
                  onChange={(e) => setEditProfileForm({ ...editProfileForm, age: e.target.value })}
                  placeholder="25"
                  min={1}
                  max={100}
                />
              </div>
              {categories.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Category</label>
                  <select
                    className="input w-full text-sm bg-gray-800 border-gray-700"
                    value={editProfileForm.category_id ?? ""}
                    onChange={(e) =>
                      setEditProfileForm({
                        ...editProfileForm,
                        category_id: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                  >
                    <option value="">None</option>
                    {categories.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} (₹{c.base_price.toLocaleString()})
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Batting Rating</label>
                  <input
                    type="number"
                    className="input w-full text-sm"
                    value={editProfileForm.batting_rating}
                    onChange={(e) => setEditProfileForm({ ...editProfileForm, batting_rating: Number(e.target.value) || 5 })}
                    min={1}
                    max={10}
                    step={0.5}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Bowling Rating</label>
                  <input
                    type="number"
                    className="input w-full text-sm"
                    value={editProfileForm.bowling_rating}
                    onChange={(e) => setEditProfileForm({ ...editProfileForm, bowling_rating: Number(e.target.value) || 5 })}
                    min={1}
                    max={10}
                    step={0.5}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Fielding Rating</label>
                  <input
                    type="number"
                    className="input w-full text-sm"
                    value={editProfileForm.fielding_rating}
                    onChange={(e) => setEditProfileForm({ ...editProfileForm, fielding_rating: Number(e.target.value) || 5 })}
                    min={1}
                    max={10}
                    step={0.5}
                  />
                </div>
              </div>

              <div className="mt-6 flex justify-end gap-3">
                <button
                  type="button"
                  className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white transition-colors"
                  onClick={() => { setEditProfileOpen(false); setEditPlayerId(null); }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary px-6 py-2"
                  disabled={editingProfile}
                >
                  {editingProfile ? "Saving..." : "Save Changes"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* CSV Import Dialog */}
      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 max-w-md w-full shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-1">Import Players from CSV</h3>
            <p className="text-sm text-gray-400 mb-4">
              Upload a CSV with columns: <code className="text-gray-300">name,email,password,phone,batting_rating,bowling_rating,fielding_rating</code>
              <br />
              <span className="text-xs text-gray-500 block mt-1">Only <b>name</b> and <b>email</b> are required.</span>
            </p>

            <div className="space-y-3">
              <label className="block text-xs font-medium text-gray-400 uppercase">CSV File</label>
              <input
                type="file"
                accept=".csv"
                className="block w-full text-sm text-gray-400
                  file:mr-4 file:py-2 file:px-4
                  file:rounded-full file:border-0
                  file:text-sm file:font-semibold
                  file:bg-amber-500/10 file:text-amber-400
                  hover:file:bg-amber-500/20"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) setCsvFile(f);
                }}
              />
              {csvFile && (
                <p className="text-xs text-gray-500">{csvFile.name}</p>
              )}
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white transition-colors"
                onClick={() => { setImportOpen(false); setCsvFile(null); }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary px-6 py-2"
                disabled={!csvFile || importing}
                onClick={handleImportCsv}
              >
                {importing ? "Importing..." : "Import Players"}
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
  const [eligiblePlayers, setEligiblePlayers] = useState<User[]>([]);
  const [auctionPlayers, setAuctionPlayers] = useState<AuctionPlayer[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [bidSlabs, setBidSlabs] = useState<BidSlab[]>([]);
  const [categories, setCategories] = useState<PlayerCategory[]>([]);
  const [tab, setTab] = useState<"players" | "teams" | "auctioneer" | "settings" | "bid-slabs" | "categories">("players");
  const [teamForm, setTeamForm] = useState({ name: "", color: "#3B82F6" });
  const [settingsForm, setSettingsForm] = useState({ budget: "100000", max_players: "15", base_price: "100", no_timer: false });
  const [slabForm, setSlabForm] = useState({ min_value: 0, max_value: 0, increment: 1000, sort_order: 0 });
  const [catForm, setCatForm] = useState({ name: "", base_price: 1000, sort_order: 0, auction_first: false });
  const [toast, setToast] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [markingReady, setMarkingReady] = useState(false);
  const [scheduleInput, setScheduleInput] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [creatingSlab, setCreatingSlab] = useState(false);
  const [creatingCat, setCreatingCat] = useState(false);
  const [createProfileOpen, setCreateProfileOpen] = useState(false);
  const [profileForm, setProfileForm] = useState({
    name: "",
    email: "",
    phone: "",
    age: "",
    batting_rating: 5,
    bowling_rating: 5,
    fielding_rating: 5,
    password: "",
  });
  const [creatingProfile, setCreatingProfile] = useState(false);

  const showToast = (type: "ok" | "err", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 3500);
  };

  const fetchAll = useCallback(async () => {
    const [evRes, eligible, aPlayers, teamsRes, readinessRes, slabsRes, catsRes] = await Promise.all([
      api.get(`/auction/events/${eid}`).catch(() => null),
      api.get(`/organizer/events/${eid}/eligible-players`),
      api.get(`/organizer/events/${eid}/players`),
      api.get(`/organizer/events/${eid}/teams`),
      api.get(`/events/${eid}/readiness`),
      api.get(`/organizer/events/${eid}/bid-slabs`),
      api.get(`/organizer/events/${eid}/categories`),
    ]);
    if (evRes) {
      setEvent(evRes.data);
      setScheduleInput(toLocalDatetimeValue(evRes.data.scheduled_at));
      setSettingsForm({
        budget: evRes.data.team_budget?.toString() || "100000",
        max_players: evRes.data.team_max_players?.toString() || "15",
        base_price: evRes.data.player_base_price?.toString() || "100",
        no_timer: evRes.data.no_timer ?? false,
      });
    }
    setEligiblePlayers(eligible.data);
    setAuctionPlayers(aPlayers.data);
    setTeams(teamsRes.data);
    setReadiness(readinessRes.data);
    setBidSlabs(slabsRes.data);
    setCategories(catsRes.data);
  }, [eid]);

  useEffect(() => {
    fetchAll().catch((err) => {
      // Only redirect on 401 auth errors; other errors stay visible
      if (err.response?.status === 401) {
        router.push("/auth/login");
      }
    });
  }, [eid, fetchAll]);

  const addPlayer = async (playerId: number, categoryId?: number) => {
    try {
      await api.post(`/organizer/events/${eid}/players`, { player_id: playerId, category_id: categoryId });
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to add player");
    }
  };

  const removePlayer = async (playerId: number) => {
    try {
      await api.delete(`/organizer/events/${eid}/players/${playerId}`);
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to remove player");
    }
  };

  const createTeam = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post(`/organizer/events/${eid}/teams`, {
        name: teamForm.name,
        color: teamForm.color,
      });
      setTeamForm({ name: "", color: "#3B82F6" });
      await fetchAll();
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
        player_base_price: parseInt(settingsForm.base_price),
        no_timer: settingsForm.no_timer,
      };
      const { data } = await api.patch(`/organizer/events/${eid}/settings`, payload);
      setEvent(data);
      await fetchAll();
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
      showToast("ok", "Logo uploaded successfully");
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
      showToast("ok", "Logo removed");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to remove logo");
    }
  };

  // ── Bid Slabs ──
  const createBidSlab = async () => {
    setCreatingSlab(true);
    try {
      const { data } = await api.post(`/organizer/events/${eid}/bid-slabs`, {
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
      const { data } = await api.post(`/organizer/events/${eid}/categories`, {
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

  const createPlayerProfile = async () => {
    setCreatingProfile(true);
    try {
      const { data } = await api.post(`/organizer/events/${eid}/players/create-profile`, {
        name: profileForm.name,
        email: profileForm.email,
        phone: profileForm.phone || undefined,
        age: profileForm.age ? Number(profileForm.age) : undefined,
        batting_rating: profileForm.batting_rating,
        bowling_rating: profileForm.bowling_rating,
        fielding_rating: profileForm.fielding_rating,
        password: profileForm.password || undefined,
      });
      showToast("ok", `Player profile created: ${data.name}`);
      setProfileForm({
        name: "", email: "", phone: "", age: "", batting_rating: 5, bowling_rating: 5, fielding_rating: 5, password: "",
      });
      setCreateProfileOpen(false);
      await fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showToast("err", msg || "Failed to create player profile");
    } finally {
      setCreatingProfile(false);
    }
  };

  const addedPlayerIds = new Set(auctionPlayers.map((ap) => ap.player_id));
  const playerMap = Object.fromEntries(eligiblePlayers.map((p) => [p.id, p]));

  const scheduledDate = event?.scheduled_at ? new Date(event.scheduled_at) : null;
  const now = new Date();
  const isLive =
    !!event &&
    (event.status === "active" ||
      event.status === "paused" ||
      event.status === "completed" ||
      (event.status === "ready" && scheduledDate !== null && scheduledDate <= now));
  const isDraft = event?.status === "draft";

  const TABS = [
    { key: "players", label: "Players", count: auctionPlayers.length },
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

            {isDraft && (
              <button
                className="btn-primary"
                onClick={markReady}
                disabled={markingReady || !readiness?.ready}
              >
                {markingReady ? "Checking..." : "Mark Ready"}
              </button>
            )}
            {!isDraft && !isLive && (
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
            {isLive && (
              <span className="text-green-400 text-sm font-medium">✓ Event is live</span>
            )}
          </div>
        </div>

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
                eligiblePlayers={eligiblePlayers}
                auctionPlayers={auctionPlayers}
                onAdd={addPlayer}
                onRemove={removePlayer}
                onInvited={fetchAll}
                onToast={showToast}
                editable={isDraft}
                eventStatus={event?.status}
                categories={categories}
                createProfileOpen={createProfileOpen}
                setCreateProfileOpen={setCreateProfileOpen}
                profileForm={profileForm}
                setProfileForm={setProfileForm}
                creatingProfile={creatingProfile}
                createPlayerProfile={createPlayerProfile}
              />
            )}

            {/* Teams tab */}
            {tab === "teams" && (
              <TeamsTab
                eventId={eid}
                teams={teams}
                eligiblePlayers={eligiblePlayers}
                teamForm={teamForm}
                setTeamForm={setTeamForm}
                onCreateTeam={createTeam}
                onAssignCaptain={assignCaptain}
                onUpdateTeam={updateTeam}
                onDeleteTeam={deleteTeam}
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
                  Search by name or email. If the person isn't registered yet, they'll receive an invite link.
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
                {!isDraft && (
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
                    {/* Event Logo */}
                    <div>
                      <label className="label text-gray-300 mb-1 block">Event Logo</label>
                      <p className="text-xs text-gray-500 mb-3">
                        Upload a logo for your event. It will be displayed on event cards.
                      </p>
                      <div className="flex items-center gap-4">
                        {event?.logo ? (
                          <div className="relative group">
                            <div className="w-20 h-20 rounded-xl overflow-hidden bg-gray-800 border border-gray-700">
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
                      <div className="flex items-center gap-2 mt-2">
                        <input
                          type="checkbox"
                          checked={settingsForm.no_timer}
                          onChange={(e) =>
                            isDraft && setSettingsForm({ ...settingsForm, no_timer: e.target.checked })
                          }
                          disabled={!isDraft}
                        />
                        <span className="text-xs text-gray-300">
                          Disable auction timer (hammer manually)
                        </span>
                      </div>
                    </div>

                    {isDraft && (
                      <div className="pt-2">
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={saveSettings}
                          disabled={savingSettings}
                        >
                          {savingSettings ? "Saving..." : "Save Settings"}
                        </button>
                      </div>
                    )}
                  </div>

                  {!isDraft && (
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
                  Categories marked "Auction First" will be auctioned before the random pool.
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

            {/* Create Player Profile dialog */}
            {createProfileOpen && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
                <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 max-w-md w-full shadow-2xl">
                  <h3 className="text-lg font-bold text-white mb-1">Create Player Profile</h3>
                  <p className="text-sm text-gray-400 mb-6">
                    The organiser creates the player account directly. The player can log in later
                    with the password below (or via magic code / reset password).
                  </p>

                  <form onSubmit={(e) => { e.preventDefault(); createPlayerProfile(); }} className="space-y-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Full Name</label>
                      <input
                        type="text"
                        className="input w-full text-sm"
                        value={profileForm.name}
                        onChange={(e) => setProfileForm({ ...profileForm, name: e.target.value })}
                        placeholder="Virat Kohli"
                        required
                        autoFocus
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Email</label>
                      <input
                        type="email"
                        className="input w-full text-sm"
                        value={profileForm.email}
                        onChange={(e) => setProfileForm({ ...profileForm, email: e.target.value })}
                        placeholder="virat@example.com"
                        required
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Phone</label>
                      <input
                        type="tel"
                        className="input w-full text-sm"
                        value={profileForm.phone}
                        onChange={(e) => setProfileForm({ ...profileForm, phone: e.target.value })}
                        placeholder="+91 98765 43210"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Age</label>
                      <input
                        type="number"
                        className="input w-full text-sm"
                        value={profileForm.age}
                        onChange={(e) => setProfileForm({ ...profileForm, age: e.target.value })}
                        placeholder="25"
                        min={1}
                        max={100}
                      />
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Batting Rating</label>
                        <input
                          type="number"
                          className="input w-full text-sm"
                          value={profileForm.batting_rating}
                          onChange={(e) => setProfileForm({ ...profileForm, batting_rating: Number(e.target.value) || 5 })}
                          min={1}
                          max={10}
                          step={0.5}
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Bowling Rating</label>
                        <input
                          type="number"
                          className="input w-full text-sm"
                          value={profileForm.bowling_rating}
                          onChange={(e) => setProfileForm({ ...profileForm, bowling_rating: Number(e.target.value) || 5 })}
                          min={1}
                          max={10}
                          step={0.5}
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Fielding Rating</label>
                        <input
                          type="number"
                          className="input w-full text-sm"
                          value={profileForm.fielding_rating}
                          onChange={(e) => setProfileForm({ ...profileForm, fielding_rating: Number(e.target.value) || 5 })}
                          min={1}
                          max={10}
                          step={0.5}
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-400 uppercase mb-1">Password</label>
                      <input
                        type="password"
                        className="input w-full text-sm"
                        value={profileForm.password}
                        onChange={(e) => setProfileForm({ ...profileForm, password: e.target.value })}
                        placeholder="password123 (optional)"
                      />
                      <p className="text-[10px] text-gray-500 mt-0.5">
                        Leave blank to set a default; player can reset via magic code.
                      </p>
                    </div>

                    <div className="mt-6 flex justify-end gap-3">
                      <button
                        type="button"
                        className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white transition-colors"
                        onClick={() => { setCreateProfileOpen(false); }}
                      >
                        Cancel
                      </button>
                      <button
                        type="submit"
                        className="btn-primary px-6 py-2"
                        disabled={creatingProfile}
                      >
                        {creatingProfile ? "Creating..." : "Create Player"}
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
