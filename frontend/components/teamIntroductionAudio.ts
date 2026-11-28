export interface TeamIntroAudioController {
  playOpening: () => void;
  playTeamReveal: (index: number) => void;
  playPurseReveal: () => void;
  playFinish: () => void;
  setMuted: (muted: boolean) => void;
  setVolume: (volume: number) => void;
  stop: () => void;
}

/**
 * Original, locally generated auction-introduction audio.
 * No external media, network requests, or copyrighted recordings are used.
 * Create this directly inside the Start button gesture to satisfy browser
 * audio-activation policies.
 */
export function createTeamIntroAudioController(): TeamIntroAudioController | null {
  if (typeof window === "undefined" || !window.AudioContext) return null;

  let context: AudioContext;
  try {
    context = new window.AudioContext();
  } catch {
    return null;
  }

  const master = context.createGain();
  master.gain.value = 0.55;
  master.connect(context.destination);

  const ambience = context.createGain();
  ambience.gain.value = 0.18;
  ambience.connect(master);

  const lowPass = context.createBiquadFilter();
  lowPass.type = "lowpass";
  lowPass.frequency.value = 520;
  lowPass.connect(ambience);

  const padA = context.createOscillator();
  padA.type = "triangle";
  padA.frequency.value = 110;
  padA.connect(lowPass);
  padA.start();

  const padB = context.createOscillator();
  padB.type = "sine";
  padB.frequency.value = 164.81;
  padB.connect(lowPass);
  padB.start();

  let volume = 0.8;
  let muted = false;
  let stopped = false;
  const oneShots: OscillatorNode[] = [];

  void context.resume().catch(() => undefined);

  const setMaster = () => {
    if (stopped) return;
    master.gain.setTargetAtTime(muted ? 0 : 0.55 * volume, context.currentTime, 0.06);
  };

  const tone = (
    frequency: number,
    startAt: number,
    duration: number,
    peak: number,
    type: OscillatorType = "sine"
  ) => {
    if (stopped) return;
    const oscillator = context.createOscillator();
    const envelope = context.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency, startAt);
    envelope.gain.setValueAtTime(0.0001, startAt);
    envelope.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), startAt + 0.025);
    envelope.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
    oscillator.connect(envelope);
    envelope.connect(master);
    oscillator.start(startAt);
    oscillator.stop(startAt + duration + 0.04);
    oneShots.push(oscillator);
    oscillator.onended = () => {
      const index = oneShots.indexOf(oscillator);
      if (index >= 0) oneShots.splice(index, 1);
      oscillator.disconnect();
      envelope.disconnect();
    };
  };

  const whoosh = (startAt: number, peak = 0.075) => {
    if (stopped) return;
    const length = Math.max(1, Math.floor(context.sampleRate * 0.32));
    const buffer = context.createBuffer(1, length, context.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) {
      channel[i] = (Math.random() * 2 - 1) * (1 - i / length);
    }
    const source = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const envelope = context.createGain();
    source.buffer = buffer;
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(420, startAt);
    filter.frequency.exponentialRampToValueAtTime(2600, startAt + 0.24);
    filter.Q.value = 0.7;
    envelope.gain.setValueAtTime(0.0001, startAt);
    envelope.gain.linearRampToValueAtTime(peak, startAt + 0.08);
    envelope.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.31);
    source.connect(filter);
    filter.connect(envelope);
    envelope.connect(master);
    source.start(startAt);
    source.stop(startAt + 0.34);
    source.onended = () => {
      source.disconnect();
      filter.disconnect();
      envelope.disconnect();
    };
  };

  return {
    playOpening: () => {
      const now = context.currentTime + 0.02;
      whoosh(now, 0.17);
      tone(73.42, now + 0.02, 0.85, 0.17, "triangle");
      tone(220, now + 0.2, 0.75, 0.065, "sine");
    },
    playTeamReveal: (index: number) => {
      const now = context.currentTime + 0.015;
      const notes = [293.66, 329.63, 392, 440, 392, 349.23];
      whoosh(now, 0.085);
      tone(notes[index % notes.length], now + 0.045, 0.32, 0.085, "triangle");
      tone(notes[index % notes.length] * 2, now + 0.075, 0.22, 0.028, "sine");
    },
    playPurseReveal: () => {
      const now = context.currentTime + 0.02;
      [523.25, 659.25, 783.99].forEach((note, index) => {
        tone(note, now + index * 0.075, 0.16, 0.04, "sine");
      });
    },
    playFinish: () => {
      const now = context.currentTime + 0.02;
      whoosh(now, 0.18);
      [196, 246.94, 293.66, 392].forEach((note, index) => {
        tone(note, now + index * 0.025, 1.05, 0.07, "triangle");
      });
    },
    setMuted: (nextMuted: boolean) => {
      muted = nextMuted;
      setMaster();
    },
    setVolume: (nextVolume: number) => {
      volume = Math.min(1, Math.max(0, nextVolume));
      setMaster();
    },
    stop: () => {
      if (stopped) return;
      stopped = true;
      try {
        padA.stop();
        padB.stop();
      } catch {
        // Oscillators may already have stopped during browser teardown.
      }
      oneShots.forEach((oscillator) => {
        try {
          oscillator.stop();
        } catch {
          // Ignore already-ended one-shot effects.
        }
      });
      master.disconnect();
      void context.close().catch(() => undefined);
    },
  };
}
