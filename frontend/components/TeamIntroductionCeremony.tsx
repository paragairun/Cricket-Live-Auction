"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { resolveMediaUrl } from "@/lib/mediaUrl";
import type { TeamState } from "@/store/auction";
import type { TeamIntroAudioController } from "./teamIntroductionAudio";

interface PlayerDetail {
  player_id: number;
  name: string;
  profile_photo?: string | null;
}

interface TeamIntroductionCeremonyProps {
  teams: TeamState[];
  playerDetails: Record<number, PlayerDetail>;
  audio: TeamIntroAudioController | null;
  onComplete: () => void;
}

type CeremonyPhase = "opening" | "team" | "final";

const money = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

export default function TeamIntroductionCeremony({
  teams,
  playerDetails,
  audio,
  onComplete,
}: TeamIntroductionCeremonyProps) {
  const [phase, setPhase] = useState<CeremonyPhase>("opening");
  const [teamIndex, setTeamIndex] = useState(0);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(0.8);
  const [animatedPurse, setAnimatedPurse] = useState(0);
  const onCompleteRef = useRef(onComplete);
  const openingAudioPlayedRef = useRef(false);
  const finalAudioPlayedRef = useRef(false);
  const revealedTeamAudioIdRef = useRef<number | null>(null);
  onCompleteRef.current = onComplete;

  // The complete ceremony stays within roughly 10–15 seconds for normal
  // team counts. Larger auctions use a quicker, simultaneous reveal per team.
  const teamDuration = useMemo(
    () => (teams.length ? Math.max(750, Math.min(2300, Math.floor(10500 / teams.length))) : 750),
    [teams.length]
  );
  const team = teams[teamIndex];

  const finish = () => {
    audio?.stop();
    onCompleteRef.current();
  };

  useEffect(() => {
    if (!openingAudioPlayedRef.current) {
      openingAudioPlayedRef.current = true;
      audio?.playOpening();
    }
    const timer = window.setTimeout(
      () => setPhase(teams.length ? "team" : "final"),
      1200
    );
    return () => window.clearTimeout(timer);
  }, [audio, teams.length]);

  useEffect(() => {
    if (phase !== "team" || !team) return;
    if (revealedTeamAudioIdRef.current !== team.id) {
      revealedTeamAudioIdRef.current = team.id;
      audio?.playTeamReveal(teamIndex);
    }
    const purseTimer = window.setTimeout(() => audio?.playPurseReveal(), 260);
    const nextTimer = window.setTimeout(() => {
      if (teamIndex + 1 >= teams.length) {
        setPhase("final");
      } else {
        setTeamIndex((current) => current + 1);
      }
    }, teamDuration);
    return () => {
      window.clearTimeout(purseTimer);
      window.clearTimeout(nextTimer);
    };
  }, [audio, phase, teamDuration, teamIndex, team, teams.length]);

  useEffect(() => {
    if (phase !== "final") return;
    if (!finalAudioPlayedRef.current) {
      finalAudioPlayedRef.current = true;
      audio?.playFinish();
    }
    const timer = window.setTimeout(() => {
      audio?.stop();
      onCompleteRef.current();
    }, 1550);
    return () => window.clearTimeout(timer);
  }, [phase, audio]);

  useEffect(() => {
    audio?.setMuted(muted);
  }, [audio, muted]);

  useEffect(() => {
    audio?.setVolume(volume);
  }, [audio, volume]);

  useEffect(() => {
    if (phase !== "team" || !team) return;
    const target = Math.max(0, team.budget || 0);
    const duration = Math.min(620, Math.max(360, teamDuration * 0.68));
    let frame = 0;
    const startedAt = performance.now();
    setAnimatedPurse(0);
    const animate = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setAnimatedPurse(Math.round(target * eased));
      if (progress < 1) frame = window.requestAnimationFrame(animate);
    };
    frame = window.requestAnimationFrame(animate);
    return () => window.cancelAnimationFrame(frame);
  }, [phase, team?.id, team?.budget, teamDuration]);

  useEffect(() => () => audio?.stop(), [audio]);

  const captain = team?.captain_id != null
    ? playerDetails[Number(team.captain_id)]
    : undefined;
  const captainName = captain?.name || team?.captain_name || "Captain";
  const hasCaptain = team?.captain_id != null;
  const teamInitials = (team?.name || "TEAM")
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();

  return (
    <div
      className="fixed inset-0 z-[250] flex items-center justify-center overflow-hidden bg-[#050914] text-white"
      role="dialog"
      aria-modal="true"
      aria-label="Team introduction ceremony"
    >
      <div className="team-intro-ambient absolute inset-0" aria-hidden="true" />
      <div className="team-intro-lightbeam absolute inset-0" aria-hidden="true" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_45%,rgba(30,64,112,0.42),transparent_62%)]" />

      <div className="absolute left-0 right-0 top-0 h-1 bg-white/10">
        <div
          className="h-full bg-gradient-to-r from-amber-500 via-yellow-200 to-amber-500 transition-all duration-300"
          style={{
            width: phase === "opening"
              ? "5%"
              : phase === "final"
                ? "100%"
                : `${5 + ((teamIndex + 1) / Math.max(teams.length, 1)) * 90}%`,
          }}
        />
      </div>

      <div className="absolute right-4 top-4 z-20 flex items-center gap-3 sm:right-7 sm:top-6">
        <label className="flex items-center gap-2 rounded-full border border-white/15 bg-black/40 px-3 py-2 text-xs text-white/80 backdrop-blur">
          <span aria-hidden="true">{muted ? "🔇" : "🔊"}</span>
          <span className="sr-only">Introduction volume</span>
          <input
            aria-label="Introduction volume"
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={volume}
            onChange={(event) => setVolume(Number(event.target.value))}
            className="w-16 accent-amber-300 sm:w-20"
          />
          <button
            type="button"
            onClick={() => setMuted((current) => !current)}
            className="font-bold text-amber-200 hover:text-white"
          >
            {muted ? "UNMUTE" : "MUTE"}
          </button>
        </label>
        <button
          type="button"
          onClick={finish}
          className="rounded-full border border-white/20 bg-black/45 px-4 py-2 text-[11px] font-extrabold tracking-[0.16em] text-white/85 backdrop-blur transition hover:border-amber-300 hover:text-amber-200"
        >
          SKIP INTRO
        </button>
      </div>

      <div className="relative z-10 mx-auto flex h-full w-full max-w-6xl flex-col items-center justify-center px-5 py-20 text-center sm:px-10">
        {phase === "opening" && (
          <div className="team-intro-enter">
            <p className="mb-5 text-xs font-black uppercase tracking-[0.48em] text-amber-300 sm:text-sm">
              The teams are ready
            </p>
            <h1 className="text-4xl font-black uppercase leading-none tracking-tight sm:text-6xl lg:text-7xl">
              Let the battle
              <span className="mt-2 block bg-gradient-to-r from-amber-200 via-yellow-400 to-amber-200 bg-clip-text text-transparent">
                begin
              </span>
            </h1>
            <p className="mt-6 text-xs font-semibold uppercase tracking-[0.32em] text-slate-300/75">
              Introducing the teams
            </p>
          </div>
        )}

        {phase === "team" && team && (
          <div key={team.id} className="team-intro-enter w-full">
            <p className="mb-3 text-[10px] font-black uppercase tracking-[0.42em] text-amber-300 sm:text-xs">
              Team {teamIndex + 1} of {teams.length}
            </p>
            <h2 className="mb-7 text-3xl font-black uppercase tracking-tight text-white drop-shadow-lg sm:mb-9 sm:text-5xl">
              {team.name}
            </h2>

            <div className={`mx-auto flex w-full max-w-2xl items-center justify-center gap-5 sm:gap-12 ${hasCaptain ? "" : "max-w-sm"}`}>
              <div className="flex min-w-0 flex-1 flex-col items-center">
                <div className="flex h-32 w-32 items-center justify-center overflow-hidden rounded-2xl border border-amber-200/45 bg-gradient-to-br from-white/15 to-white/[0.03] p-3 shadow-[0_0_42px_rgba(245,190,70,0.13)] sm:h-52 sm:w-52 sm:rounded-3xl sm:p-5">
                  {team.logo ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={resolveMediaUrl(team.logo)}
                      alt={`${team.name} logo`}
                      className="h-full w-full object-contain drop-shadow-xl"
                    />
                  ) : (
                    <span className="text-4xl font-black tracking-tight text-amber-200 sm:text-6xl">
                      {teamInitials}
                    </span>
                  )}
                </div>
                <p className="mt-3 text-[10px] font-black uppercase tracking-[0.22em] text-amber-200 sm:mt-4 sm:text-xs">
                  Team
                </p>
                <p className="mt-1 max-w-full truncate text-sm font-bold text-white sm:text-base">
                  {team.name}
                </p>
              </div>

              {hasCaptain && (
                <>
                  <div className="h-28 w-px shrink-0 bg-gradient-to-b from-transparent via-amber-300/70 to-transparent sm:h-48" />
                  <div className="flex min-w-0 flex-1 flex-col items-center">
                    <div className="h-32 w-32 overflow-hidden rounded-2xl border border-amber-200/45 bg-gradient-to-br from-slate-600 to-slate-950 shadow-[0_0_42px_rgba(245,190,70,0.13)] sm:h-52 sm:w-52 sm:rounded-3xl">
                      {captain?.profile_photo ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={resolveMediaUrl(captain.profile_photo)}
                          alt={captainName}
                          className="h-full w-full object-cover object-top"
                        />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-4xl font-black text-amber-100 sm:text-6xl">
                          {captainName.slice(0, 1).toUpperCase()}
                        </div>
                      )}
                    </div>
                    <p className="mt-3 text-[10px] font-black uppercase tracking-[0.22em] text-amber-200 sm:mt-4 sm:text-xs">
                      Captain
                    </p>
                    <p className="mt-1 max-w-full truncate text-sm font-bold text-white sm:text-base">
                      {captainName}
                    </p>
                  </div>
                </>
              )}
            </div>

            <div className="mx-auto mt-7 max-w-md border-t border-white/15 pt-5 sm:mt-9 sm:pt-6">
              <p className="text-[10px] font-bold uppercase tracking-[0.38em] text-slate-300/80 sm:text-xs">
                Initial purse
              </p>
              <p className="mt-2 text-3xl font-black tabular-nums text-amber-200 drop-shadow-[0_0_18px_rgba(245,190,70,0.25)] sm:text-5xl">
                {money.format(animatedPurse)}
              </p>
              <p className="mt-1 text-[9px] font-semibold uppercase tracking-[0.24em] text-slate-400 sm:text-[10px]">
                Available auction budget
              </p>
            </div>
          </div>
        )}

        {phase === "final" && (
          <div className="team-intro-enter">
            <p className="mb-5 text-xs font-black uppercase tracking-[0.46em] text-amber-300 sm:text-sm">
              All teams introduced
            </p>
            <h2 className="text-4xl font-black uppercase leading-none tracking-tight sm:text-6xl lg:text-7xl">
              The auction
              <span className="mt-2 block bg-gradient-to-r from-amber-200 via-yellow-400 to-amber-200 bg-clip-text text-transparent">
                begins now!
              </span>
            </h2>
            <p className="mt-6 text-xs font-semibold uppercase tracking-[0.28em] text-slate-300/75">
              Let the bidding begin
            </p>
          </div>
        )}
      </div>

      <div className="absolute bottom-5 left-0 right-0 z-10 flex justify-center gap-2">
        {teams.map((item, index) => (
          <span
            key={item.id}
            className={`h-1 rounded-full transition-all duration-300 ${index === teamIndex && phase === "team" ? "w-8 bg-amber-300" : index < teamIndex || phase === "final" ? "w-3 bg-amber-200/70" : "w-3 bg-white/20"}`}
          />
        ))}
      </div>

      <style jsx>{`
        .team-intro-ambient {
          background:
            radial-gradient(ellipse at 50% 100%, rgba(22, 73, 116, 0.34), transparent 48%),
            linear-gradient(135deg, #050914 0%, #0b1427 48%, #050914 100%);
        }
        .team-intro-lightbeam {
          opacity: 0.24;
          background: conic-gradient(from 250deg at 50% -20%, transparent 0deg, rgba(255, 218, 140, 0.18) 18deg, transparent 38deg, transparent 100deg, rgba(255, 255, 255, 0.08) 120deg, transparent 142deg);
          animation: beam-drift 8s ease-in-out infinite alternate;
        }
        .team-intro-enter {
          animation: team-reveal 480ms cubic-bezier(0.2, 0.75, 0.25, 1) both;
        }
        @keyframes team-reveal {
          from { opacity: 0; transform: translateY(22px) scale(0.975); filter: blur(5px); }
          to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
        }
        @keyframes beam-drift {
          from { transform: translateX(-3%) scale(1); }
          to { transform: translateX(3%) scale(1.06); }
        }
        @media (prefers-reduced-motion: reduce) {
          .team-intro-enter, .team-intro-lightbeam { animation: none !important; }
        }
      `}</style>
    </div>
  );
}
