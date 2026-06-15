const MUSIC_START_VOLUME = 0.2;
const MUSIC_VOLUME_SCALE = 0.5;
const MUSIC_PREVIOUS_THRESHOLD_SECONDS = 5;
const MUSIC_HISTORY_LIMIT = 40;
const SVG_NS = "http://www.w3.org/2000/svg";
const ORDER_STAT_TYPES = [
  {
    key: "move",
    title: "Move orders by nation",
    empty: "No move orders yet",
  },
  {
    key: "hold",
    title: "Hold orders by nation",
    empty: "No hold orders yet",
  },
  {
    key: "support",
    title: "Support orders by nation",
    empty: "No support orders yet",
  },
  {
    key: "retreat",
    title: "Retreat orders by nation",
    empty: "No retreat orders yet",
  },
];

const state = {
  setups: [],
  runs: [],
  activeRun: null,
  liveProvider: {status: "available"},
  replay: null,
  frameIndex: 0,
  timer: null,
  activeConversationKey: null,
  activeDetailTab: "orders",
  liveSource: null,
  liveRunId: null,
  liveRunMode: null,
  liveRunActive: false,
  liveLaunchPending: false,
  liveRefreshTimer: null,
  runListRefreshTimer: null,
  liveEvents: [],
  followLiveTail: false,
  conversationActivityCounts: new Map(),
  unreadConversationKeys: new Set(),
  trackUnreadConversations: false,
  music: {
    tracks: [],
    currentIndex: 0,
    repeatOne: false,
    shuffle: false,
    wantPlaying: true,
    pendingAutoplay: false,
    history: [],
    unlockHandler: null,
  },
};

const els = {
  launcherView: document.querySelector("#launcherView"),
  dashboardView: document.querySelector("#dashboardView"),
  topbar: document.querySelector(".topbar"),
  launcherStatus: document.querySelector("#launcherStatus"),
  statusText: document.querySelector("#statusText"),
  launchForm: document.querySelector("#launchForm"),
  setupField: document.querySelector("#setupField"),
  setupSelect: document.querySelector("#setupSelect"),
  runMode: document.querySelector("#runMode"),
  modeTabs: [...document.querySelectorAll(".mode-tab")],
  launcherRunPicker: document.querySelector("#launcherRunPicker"),
  launcherRunSelect: document.querySelector("#launcherRunSelect"),
  apiKeyField: document.querySelector("#apiKeyField"),
  apiKeyInput: document.querySelector("#apiKeyInput"),
  launchBtn: document.querySelector("#launchBtn"),
  launcherWarning: document.querySelector("#launcherWarning"),
  runStateBanner: document.querySelector("#runStateBanner"),
  runStateText: document.querySelector("#runStateText"),
  detachLiveBtn: document.querySelector("#detachLiveBtn"),
  postRunActions: document.querySelector("#postRunActions"),
  newRunBtn: document.querySelector("#newRunBtn"),
  runSelect: document.querySelector("#runSelect"),
  setupName: document.querySelector("#setupName"),
  mapName: document.querySelector("#mapName"),
  powerDetails: document.querySelector("#powerDetails"),
  liveEvents: document.querySelector("#liveEvents"),
  boardFrame: document.querySelector("#boardFrame"),
  conversationTabs: document.querySelector("#conversationTabs"),
  messageList: document.querySelector("#messageList"),
  detailTabs: [...document.querySelectorAll(".detail-tab")],
  ordersPane: document.querySelector("#ordersPane"),
  reasoningPane: document.querySelector("#reasoningPane"),
  statsPane: document.querySelector("#statsPane"),
  infoPane: document.querySelector("#infoPane"),
  livePane: document.querySelector("#livePane"),
  prevBtn: document.querySelector("#prevBtn"),
  playBtn: document.querySelector("#playBtn"),
  nextBtn: document.querySelector("#nextBtn"),
  phaseName: document.querySelector("#phaseName"),
  phaseIndex: document.querySelector("#phaseIndex"),
  timelineState: document.querySelector("#timelineState"),
  jumpLiveBtn: document.querySelector("#jumpLiveBtn"),
  phaseSlider: document.querySelector("#phaseSlider"),
  musicPlayer: document.querySelector("#musicPlayer"),
  musicAudio: document.querySelector("#musicAudio"),
  musicSelect: document.querySelector("#musicSelect"),
  musicPlayBtn: document.querySelector("#musicPlayBtn"),
  musicPrevBtn: document.querySelector("#musicPrevBtn"),
  musicNextBtn: document.querySelector("#musicNextBtn"),
  musicRepeatBtn: document.querySelector("#musicRepeatBtn"),
  musicShuffleBtn: document.querySelector("#musicShuffleBtn"),
  musicVolume: document.querySelector("#musicVolume"),
};

async function boot() {
  bindControls();
  showLauncher();
  await Promise.all([loadSetups(), loadRuns(), loadMusic()]);
  renderEmptyBoard("No active run");
  renderEmptyMessages("No active run");
  renderPhaseDetails([], []);
  renderLiveEvents();
  setControlsEnabled(false);
}

function bindControls() {
  els.launchForm.addEventListener("submit", startLiveRun);
  els.modeTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      selectLaunchMode(tab.dataset.mode);
    });
  });
  els.launcherRunSelect.addEventListener("change", syncLaunchMode);
  els.newRunBtn.addEventListener("click", showLauncher);
  els.detachLiveBtn.addEventListener("click", detachLiveView);
  els.runSelect.addEventListener("change", () => {
    stopPlayback();
    state.trackUnreadConversations = false;
    resetConversationIndicators();
    openReplayOrActiveRun(els.runSelect.value);
  });
  els.prevBtn.addEventListener("click", () => {
    stopPlayback();
    showFrame(state.frameIndex - 1, {userSelected: true});
  });
  els.nextBtn.addEventListener("click", () => {
    stopPlayback();
    showFrame(state.frameIndex + 1, {userSelected: true});
  });
  els.playBtn.addEventListener("click", togglePlayback);
  els.phaseSlider.addEventListener("input", () => {
    stopPlayback();
    showFrame(Number.parseInt(els.phaseSlider.value, 10), {userSelected: true});
  });
  els.jumpLiveBtn.addEventListener("click", jumpToLiveTail);
  els.detailTabs.forEach((tab) => {
    tab.addEventListener("click", () => setDetailTab(tab.dataset.detailTab));
  });
  els.musicSelect.addEventListener("change", selectMusicTrack);
  els.musicPlayBtn.addEventListener("click", toggleMusicPlayback);
  els.musicPrevBtn.addEventListener("click", playPreviousMusic);
  els.musicNextBtn.addEventListener("click", () => playNextMusic({rememberCurrent: true}));
  els.musicRepeatBtn.addEventListener("click", toggleMusicRepeat);
  els.musicShuffleBtn.addEventListener("click", toggleMusicShuffle);
  els.musicVolume.addEventListener("input", setMusicVolume);
  els.musicAudio.addEventListener("ended", handleMusicEnded);
  els.musicAudio.addEventListener("play", updateMusicButtons);
  els.musicAudio.addEventListener("pause", updateMusicButtons);
}

function selectLaunchMode(mode) {
  if (!["live", "replay"].includes(mode)) {
    return;
  }
  if (els.runMode.value === mode) {
    return;
  }
  els.runMode.value = mode;
  clearLauncherWarning();
  syncLaunchMode();
}

function placeMusicPlayerInLauncher() {
  if (els.musicPlayer.parentElement === document.body) {
    return;
  }
  document.body.insertBefore(els.musicPlayer, els.launcherView);
}

function placeMusicPlayerInDashboard() {
  if (els.musicPlayer.parentElement === els.topbar) {
    return;
  }
  els.topbar.appendChild(els.musicPlayer);
}

async function loadSetups() {
  const response = await fetch("/api/demo-setups");
  const payload = await response.json();
  state.setups = payload.setups || [];
  els.setupSelect.replaceChildren(
    ...state.setups.map((setup) => {
      const option = document.createElement("option");
      option.value = setup.name;
      option.textContent = setup.label || setup.name;
      return option;
    }),
  );
  els.launchBtn.disabled = !state.setups.length;
  setLauncherStatus(state.setups.length ? "Choose a setup to start" : "No demo setups found");
  syncLaunchMode();
}

async function loadRuns() {
  const selectedRunId = els.runSelect.value;
  const selectedLauncherRunId = els.launcherRunSelect.value;
  const response = await fetch("/api/runs");
  const payload = await response.json();
  state.runs = payload.runs || [];
  state.activeRun = payload.active_run || state.runs.find((run) => run.is_active_live) || null;
  state.liveProvider = payload.live_provider || {status: "available"};
  if (!state.runs.length) {
    els.runSelect.replaceChildren(emptyRunOption());
    els.launcherRunSelect.replaceChildren(emptyRunOption());
    els.runSelect.disabled = true;
    els.launcherRunSelect.disabled = true;
    syncLaunchMode();
    scheduleRunListRefresh();
    return;
  }

  els.runSelect.replaceChildren(...runOptions());
  els.launcherRunSelect.replaceChildren(...runOptions());
  restoreSelectValue(els.runSelect, selectedRunId);
  restoreSelectValue(els.launcherRunSelect, selectedLauncherRunId);
  els.runSelect.disabled = false;
  els.launcherRunSelect.disabled = false;
  syncLaunchMode();
  scheduleRunListRefresh();
}

function runOptions() {
  return state.runs.map((run) => {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = isActiveRun(run) ? `LIVE - ${run.label || run.run_id}` : run.label || run.run_id;
    option.classList.toggle("is-live-run", isActiveRun(run));
    return option;
  });
}

function emptyRunOption() {
  const option = document.createElement("option");
  option.value = "";
  option.textContent = "No historic runs";
  return option;
}

function restoreSelectValue(select, value) {
  if (!value) {
    return;
  }
  if ([...select.options].some((option) => option.value === value)) {
    select.value = value;
  }
}

function runById(runId) {
  return state.runs.find((run) => run.run_id === runId) || null;
}

function isActiveRun(run) {
  return Boolean(run && run.is_active_live);
}

function scheduleRunListRefresh() {
  if (!state.activeRun) {
    if (state.runListRefreshTimer) {
      window.clearTimeout(state.runListRefreshTimer);
      state.runListRefreshTimer = null;
    }
    return;
  }
  if (state.runListRefreshTimer) {
    return;
  }
  state.runListRefreshTimer = window.setTimeout(async () => {
    state.runListRefreshTimer = null;
    try {
      await loadRuns();
    } catch {
      scheduleRunListRefresh();
    }
  }, 2500);
}

async function loadMusic() {
  els.musicVolume.value = String(Math.round(MUSIC_START_VOLUME * 100));
  setMusicVolume();
  setMusicEnabled(false);

  try {
    const response = await fetch("/api/music");
    const payload = await response.json();
    state.music.tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
  } catch {
    state.music.tracks = [];
  }

  renderMusicTracks();
  setMusicEnabled(state.music.tracks.length > 0);
  if (!state.music.tracks.length) {
    return;
  }
  setMusicTrack(0, {play: false, rememberCurrent: false});
  await playMusic();
}

function renderMusicTracks() {
  if (!state.music.tracks.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No music found";
    els.musicSelect.replaceChildren(option);
    return;
  }

  els.musicSelect.replaceChildren(
    ...state.music.tracks.map((track, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = track.label || track.filename || `Track ${index + 1}`;
      return option;
    }),
  );
}

function selectMusicTrack() {
  const nextIndex = Number.parseInt(els.musicSelect.value, 10);
  if (Number.isNaN(nextIndex)) {
    return;
  }
  setMusicTrack(nextIndex, {
    play: state.music.wantPlaying,
    rememberCurrent: true,
  });
}

function toggleMusicPlayback() {
  if (els.musicAudio.paused) {
    void playMusic();
    return;
  }
  pauseMusic();
}

async function playMusic() {
  if (!state.music.tracks.length) {
    return;
  }
  state.music.wantPlaying = true;
  if (!els.musicAudio.dataset.trackUrl) {
    setMusicTrack(state.music.currentIndex, {play: false, rememberCurrent: false});
  }

  try {
    await els.musicAudio.play();
    state.music.pendingAutoplay = false;
    clearMusicGestureUnlock();
  } catch (error) {
    if (error?.name === "NotAllowedError") {
      state.music.pendingAutoplay = true;
      installMusicGestureUnlock();
    } else {
      state.music.wantPlaying = false;
      state.music.pendingAutoplay = false;
    }
  } finally {
    updateMusicButtons();
  }
}

function pauseMusic() {
  state.music.wantPlaying = false;
  state.music.pendingAutoplay = false;
  clearMusicGestureUnlock();
  els.musicAudio.pause();
  updateMusicButtons();
}

function playNextMusic(options = {}) {
  if (!state.music.tracks.length) {
    return;
  }
  if (state.music.repeatOne) {
    restartCurrentMusic({play: state.music.wantPlaying});
    return;
  }
  setMusicTrack(nextMusicIndex(), {
    play: state.music.wantPlaying,
    rememberCurrent: Boolean(options.rememberCurrent),
  });
}

function playPreviousMusic() {
  if (!state.music.tracks.length) {
    return;
  }
  if (state.music.repeatOne) {
    restartCurrentMusic({play: state.music.wantPlaying});
    return;
  }
  if (els.musicAudio.currentTime >= MUSIC_PREVIOUS_THRESHOLD_SECONDS) {
    restartCurrentMusic({play: state.music.wantPlaying});
    return;
  }
  setMusicTrack(previousMusicIndex(), {
    play: state.music.wantPlaying,
    rememberCurrent: false,
  });
}

function handleMusicEnded() {
  if (state.music.repeatOne) {
    restartCurrentMusic({play: true});
    return;
  }
  playNextMusic({rememberCurrent: true});
}

function restartCurrentMusic(options = {}) {
  try {
    els.musicAudio.currentTime = 0;
  } catch {
    return;
  }
  if (options.play) {
    void playMusic();
  }
}

function toggleMusicRepeat() {
  state.music.repeatOne = !state.music.repeatOne;
  updateMusicButtons();
}

function toggleMusicShuffle() {
  state.music.shuffle = !state.music.shuffle;
  state.music.history = [];
  updateMusicButtons();
}

function setMusicVolume() {
  const rawValue = Number.parseInt(els.musicVolume.value, 10);
  const volume = Number.isNaN(rawValue) ? 20 : rawValue;
  els.musicAudio.volume = Math.min(Math.max((volume / 100) * MUSIC_VOLUME_SCALE, 0), 1);
}

function setMusicTrack(index, options = {}) {
  if (!state.music.tracks.length) {
    return;
  }
  const nextIndex = normalizeMusicIndex(index);
  const currentIndex = state.music.currentIndex;
  if (options.rememberCurrent && nextIndex !== currentIndex) {
    rememberMusicHistory(currentIndex);
  }
  state.music.currentIndex = nextIndex;

  const track = state.music.tracks[nextIndex];
  if (els.musicAudio.dataset.trackUrl !== track.url) {
    els.musicAudio.src = track.url;
    els.musicAudio.dataset.trackUrl = track.url;
    els.musicAudio.load();
  } else {
    restartCurrentMusic({play: false});
  }
  els.musicSelect.value = String(nextIndex);
  if (options.play) {
    void playMusic();
  } else {
    updateMusicButtons();
  }
}

function nextMusicIndex() {
  if (state.music.tracks.length <= 1) {
    return 0;
  }
  if (!state.music.shuffle) {
    return normalizeMusicIndex(state.music.currentIndex + 1);
  }

  let nextIndex = state.music.currentIndex;
  for (let attempt = 0; attempt < 8 && nextIndex === state.music.currentIndex; attempt += 1) {
    nextIndex = Math.floor(Math.random() * state.music.tracks.length);
  }
  return nextIndex === state.music.currentIndex
    ? normalizeMusicIndex(state.music.currentIndex + 1)
    : nextIndex;
}

function previousMusicIndex() {
  if (state.music.shuffle && state.music.history.length) {
    return state.music.history.pop();
  }
  return normalizeMusicIndex(state.music.currentIndex - 1);
}

function normalizeMusicIndex(index) {
  const trackCount = state.music.tracks.length;
  return ((index % trackCount) + trackCount) % trackCount;
}

function rememberMusicHistory(index) {
  state.music.history.push(index);
  if (state.music.history.length > MUSIC_HISTORY_LIMIT) {
    state.music.history.shift();
  }
}

function installMusicGestureUnlock() {
  if (state.music.unlockHandler) {
    return;
  }
  state.music.unlockHandler = (event) => {
    if (!state.music.pendingAutoplay || !state.music.wantPlaying) {
      clearMusicGestureUnlock();
      return;
    }
    if (event.target instanceof Node && els.musicPlayer.contains(event.target)) {
      return;
    }
    void playMusic();
  };
  document.addEventListener("pointerdown", state.music.unlockHandler);
  document.addEventListener("keydown", state.music.unlockHandler);
}

function clearMusicGestureUnlock() {
  if (!state.music.unlockHandler) {
    return;
  }
  document.removeEventListener("pointerdown", state.music.unlockHandler);
  document.removeEventListener("keydown", state.music.unlockHandler);
  state.music.unlockHandler = null;
}

function setMusicEnabled(enabled) {
  [
    els.musicSelect,
    els.musicPlayBtn,
    els.musicPrevBtn,
    els.musicNextBtn,
    els.musicRepeatBtn,
    els.musicShuffleBtn,
    els.musicVolume,
  ].forEach((control) => {
    control.disabled = !enabled;
  });
  updateMusicButtons();
}

function updateMusicButtons() {
  const hasTracks = state.music.tracks.length > 0;
  const isPlaying = hasTracks && !els.musicAudio.paused && !els.musicAudio.ended;

  els.musicPlayBtn.innerHTML = isPlaying ? musicPauseIcon() : musicPlayIcon();
  els.musicPlayBtn.title = isPlaying ? "Pause music" : "Play music";
  els.musicPlayBtn.setAttribute("aria-label", isPlaying ? "Pause music" : "Play music");

  els.musicRepeatBtn.classList.toggle("music-repeat-one", state.music.repeatOne);
  els.musicRepeatBtn.classList.toggle("is-active", state.music.repeatOne);
  els.musicRepeatBtn.title = state.music.repeatOne ? "Repeat current track" : "Repeat all";
  els.musicRepeatBtn.setAttribute(
    "aria-label",
    state.music.repeatOne ? "Repeat current track" : "Repeat all",
  );

  els.musicShuffleBtn.classList.toggle("is-active", state.music.shuffle);
  els.musicShuffleBtn.title = state.music.shuffle ? "Shuffle on" : "Shuffle off";
  els.musicShuffleBtn.setAttribute(
    "aria-label",
    state.music.shuffle ? "Shuffle on" : "Shuffle off",
  );
}

function musicPlayIcon() {
  return `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path class="filled" d="M8 5v14l11-7z"></path>
    </svg>
  `;
}

function musicPauseIcon() {
  return `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path class="filled" d="M7 5h4v14H7z"></path>
      <path class="filled" d="M13 5h4v14h-4z"></path>
    </svg>
  `;
}

async function startLiveRun(event) {
  event.preventDefault();
  const setup = els.setupSelect.value;
  const mode = els.runMode.value;
  const key = els.apiKeyInput.value.trim();
  clearLauncherWarning();
  if (mode === "replay") {
    await openHistoricReplay(els.launcherRunSelect.value);
    return;
  }
  if (state.activeRun) {
    setLauncherWarning(activeRunWarningText(state.activeRun));
    return;
  }
  if (!setup) {
    setLauncherWarning("Choose a demo setup before launching.");
    return;
  }
  if (!key) {
    setLauncherWarning(liveLaunchWarningText("missing_key"));
    return;
  }
  if (liveProviderCreditBlocked()) {
    setLauncherWarning(liveLaunchWarningText("openrouter_no_credits"));
    return;
  }

  setLaunchEnabled(false);
  state.liveEvents = [];
  state.liveLaunchPending = true;
  setLauncherStatus("Checking OpenRouter key and starting live run");

  try {
    const response = await fetch("/api/live-runs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        setup,
        mode: "live",
        openrouter_api_key: key,
      }),
    });
    els.apiKeyInput.value = "";
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Run failed to start");
    }
    const run = payload.run || {};
    state.liveRunId = run.run_id;
    state.liveRunMode = run.mode || "live";
    connectLiveEvents(payload.stream_url);
  } catch (error) {
    showLauncher();
    await loadRuns();
    setLauncherWarning(liveLaunchWarningText(error.message));
    setLaunchEnabled(true);
  }
}

async function openHistoricReplay(runId) {
  if (!runId) {
    setLauncherStatus("No historic run selected");
    return;
  }
  setLaunchEnabled(false);
  stopLiveReplayRefresh();
  state.liveRunId = null;
  state.liveRunMode = null;
  state.liveRunActive = false;
  state.liveLaunchPending = false;
  state.followLiveTail = false;
  state.liveEvents = [];
  resetConversationIndicators();
  state.trackUnreadConversations = false;
  try {
    await openReplayOrActiveRun(runId, {fromLauncher: true});
  } catch (error) {
    showLauncher();
    setLauncherStatus(error.message);
  } finally {
    setLaunchEnabled(true);
  }
}

function connectLiveEvents(streamUrl, options = {}) {
  if (state.liveSource) {
    state.liveSource.close();
  }
  state.liveSource = new EventSource(streamUrlWithAfter(streamUrl, options.afterSequence));
  state.liveSource.onmessage = (message) => {
    const event = JSON.parse(message.data);
    state.liveEvents.push(event);
    if (state.liveLaunchPending) {
      if (event.type === "run_error") {
        handleLiveLaunchFailure(event);
        return;
      }
      if (!isLiveLaunchReadyEvent(event)) {
        return;
      }
      state.liveLaunchPending = false;
      openRunDashboard(state.liveRunMode || "live", statusLabel(event.status, event.phase || state.liveRunId || ""));
    }
    renderLiveEvents();
    updateStatusFromEvent(event);
    queueLiveReplayRefresh();
    if (event.type === "game_finished" || event.type === "run_error") {
      finishLiveRun(event);
    }
  };
  state.liveSource.onerror = () => {
    if (state.liveLaunchPending) {
      void handleLiveLaunchFailure({error: "The live run stream closed before the run started."});
      return;
    }
    if (state.liveSource) {
      state.liveSource.close();
      state.liveSource = null;
    }
  };
}

function openRunDashboard(mode, statusText) {
  showDashboardRunning(mode);
  setStatus(statusText);
  renderLiveEvents();
  renderEmptyBoard("Run starting");
  renderEmptyMessages("Waiting for messages");
  renderPhaseDetails([], []);
  setDetailTab("live");
}

async function openReplayOrActiveRun(runId, options = {}) {
  if (!runId) {
    return;
  }
  const run = runById(runId);
  if (isActiveRun(run)) {
    await attachLiveRun(runId);
    return;
  }
  showDashboardFinished();
  els.runSelect.value = runId;
  await loadReplay(runId, {
    showLatest: false,
    replaceLiveEvents: true,
    prependPrelude: options.prependPrelude !== false,
  });
}

async function attachLiveRun(runId) {
  setLaunchEnabled(false);
  try {
    stopPlayback();
    stopLiveReplayRefresh();
    if (state.liveSource) {
      state.liveSource.close();
      state.liveSource = null;
    }

    const response = await fetch(`/api/live-runs/${encodeURIComponent(runId)}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Live run could not be reopened");
    }
    const run = payload.run || {};
    state.liveRunId = run.run_id || runId;
    state.liveRunMode = run.mode || "live";
    state.liveRunActive = true;
    state.liveLaunchPending = false;
    state.followLiveTail = true;
    state.liveEvents = [];
    resetConversationIndicators();
    state.trackUnreadConversations = true;
    openRunDashboard(state.liveRunMode, statusLabel(run.status, state.liveRunId));

    let afterSequence = -1;
    if (run.replay_ready) {
      try {
        await loadReplay(state.liveRunId, {showLatest: true, replaceLiveEvents: true});
        afterSequence = lastLiveEventSequence();
      } catch {
        renderEmptyBoard("Live replay still starting");
        renderEmptyMessages("Waiting for messages");
        renderPhaseDetails([], []);
      }
    }

    updateRunningBanner({status: run.status});
    connectLiveEvents(
      `/api/live-runs/${encodeURIComponent(state.liveRunId)}/events`,
      {afterSequence},
    );
  } finally {
    setLaunchEnabled(true);
  }
}

async function detachLiveView() {
  stopPlayback();
  stopLiveReplayRefresh();
  if (state.liveSource) {
    state.liveSource.close();
    state.liveSource = null;
  }
  state.liveRunId = null;
  state.liveRunMode = null;
  state.liveRunActive = false;
  state.liveLaunchPending = false;
  state.followLiveTail = false;
  state.liveEvents = [];
  resetConversationIndicators();
  state.trackUnreadConversations = false;
  showLauncher();
  els.runMode.value = "replay";
  await loadRuns();
  if (state.activeRun) {
    els.launcherRunSelect.value = state.activeRun.run_id;
  }
  syncLaunchMode();
  setLauncherStatus("Live view stopped. Choose a replay or rejoin the active run.");
}

function streamUrlWithAfter(streamUrl, afterSequence) {
  if (afterSequence == null || afterSequence < 0) {
    return streamUrl;
  }
  const separator = streamUrl.includes("?") ? "&" : "?";
  return `${streamUrl}${separator}after=${encodeURIComponent(String(afterSequence))}`;
}

function lastLiveEventSequence() {
  return state.liveEvents.reduce((highest, event) => {
    const sequence = Number(event.sequence);
    return Number.isFinite(sequence) ? Math.max(highest, sequence) : highest;
  }, -1);
}

function isLiveLaunchReadyEvent(event) {
  return ["message_sent", "orders_submitted", "reasoning_available"].includes(event.type);
}

async function handleLiveLaunchFailure(event) {
  if (state.liveSource) {
    state.liveSource.close();
    state.liveSource = null;
  }
  stopLiveReplayRefresh();
  state.liveRunId = null;
  state.liveRunMode = null;
  state.liveRunActive = false;
  state.liveLaunchPending = false;
  state.followLiveTail = false;
  state.liveEvents = [];
  showLauncher();
  await loadRuns();
  setLauncherStatus("Live run could not start");
  setLauncherWarning(liveLaunchWarningText(event.error || event.error_type || "provider_error"));
  setLaunchEnabled(true);
}

async function finishLiveRun(event) {
  const finishedRunId = state.liveRunId;
  const finishedMode = state.liveRunMode;
  if (state.liveSource) {
    state.liveSource.close();
    state.liveSource = null;
  }
  stopLiveReplayRefresh();
  state.liveRunId = null;
  state.liveRunMode = null;
  state.liveRunActive = false;
  state.liveLaunchPending = false;
  state.followLiveTail = false;
  if (event.type === "run_error") {
    showLauncher();
    await loadRuns();
    if (finishedMode === "live") {
      els.runMode.value = "live";
    }
    setLauncherStatus(finishedMode === "live" ? "Live run stopped" : "Run stopped");
    setLauncherWarning(liveLaunchWarningText(event.error || event.error_type || "provider_error"));
    setLaunchEnabled(true);
    return;
  }
  showDashboardFinished();
  await loadRuns();
  if (event.type !== "game_finished" || !finishedRunId) {
    return;
  }
  els.runSelect.value = finishedRunId;
  await loadReplay(finishedRunId, {showLatest: true, replaceLiveEvents: true});
}

async function loadReplay(runId, options = {}) {
  if (!runId) {
    return;
  }
  setStatus("Loading replay");
  setControlsEnabled(false);
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Replay failed to load");
  }
  state.frameIndex = 0;
  state.activeConversationKey = null;
  resetConversationIndicators();
  applyReplayPayload(payload, {
    showLatest: Boolean(options.showLatest),
    prependPrelude: Boolean(options.prependPrelude),
    replaceLiveEvents: options.replaceLiveEvents !== false,
  });
  setStatus(payload.label || payload.run_id);
}

function queueLiveReplayRefresh() {
  if (!state.liveRunId || state.liveRefreshTimer) {
    return;
  }
  state.liveRefreshTimer = window.setTimeout(() => {
    state.liveRefreshTimer = null;
    refreshLiveReplay();
  }, 120);
}

function stopLiveReplayRefresh() {
  if (!state.liveRefreshTimer) {
    return;
  }
  window.clearTimeout(state.liveRefreshTimer);
  state.liveRefreshTimer = null;
}

async function refreshLiveReplay() {
  if (!state.liveRunId) {
    return;
  }
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(state.liveRunId)}`);
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    applyReplayPayload(payload, {
      showLatest: state.followLiveTail,
      replaceLiveEvents: false,
    });
  } catch {
    return;
  }
}

function applyReplayPayload(payload, options = {}) {
  state.replay = displayReplayPayload(payload, {
    prependPrelude: Boolean(options.prependPrelude),
  });
  const frames = state.replay.frames;
  if (options.replaceLiveEvents && Array.isArray(payload.events)) {
    state.liveEvents = payload.events;
    renderLiveEvents();
  }
  els.phaseSlider.min = "0";
  els.phaseSlider.max = String(Math.max(0, frames.length - 1));
  renderMetadata(payload);
  if (!frames.length) {
    const hasEvents = Array.isArray(payload.events) && payload.events.length > 0;
    renderEmptyBoard("Replay unavailable");
    renderEmptyMessages(hasEvents ? "No board replay available" : "Replay unavailable");
    renderPhaseDetails([], [], null, []);
    if (hasEvents) {
      setDetailTab("live");
    }
    els.phaseSlider.value = "0";
    setControlsEnabled(false);
    updateTimelineState();
    return;
  }
  const maxIndex = frames.length - 1;
  setControlsEnabled(true);
  const shouldShowLatest = Boolean(options.showLatest) || (
    state.liveRunActive && state.followLiveTail
  );
  const nextIndex = shouldShowLatest ? maxIndex : Math.min(state.frameIndex, maxIndex);
  showFrame(nextIndex, {followLiveTail: shouldShowLatest});
}

function displayReplayPayload(payload, options = {}) {
  const frames = Array.isArray(payload.frames) ? payload.frames.map((frame) => ({...frame})) : [];
  if (!options.prependPrelude || !frames.length) {
    return {
      ...payload,
      frames,
      hasPrelude: false,
    };
  }

  return {
    ...payload,
    frames: [preludeFrame(frames[0]), ...frames],
    frame_count: frames.length + 1,
    raw_frame_count: payload.frame_count ?? frames.length,
    hasPrelude: true,
  };
}

function preludeFrame(firstFrame) {
  return {
    ...firstFrame,
    phase: "Setup",
    phase_index: -1,
    phase_type: "",
    filename_stem: "setup",
    is_final: false,
    isPrelude: true,
    source_phase: firstFrame.phase || null,
    events: [],
  };
}

function renderMetadata(replay) {
  const metadata = replay.metadata || {};
  els.setupName.textContent = metadata.setup_label || metadata.setup || metadata.setup_name || "-";
  els.mapName.textContent = metadata.map || metadata.map_name || "-";
  renderPowerDetails(metadata);
}

function renderPowerDetails(metadata) {
  const powers = Array.isArray(metadata.powers) ? metadata.powers : [];
  if (!powers.length) {
    els.powerDetails.replaceChildren();
    return;
  }
  const models = metadata.effective_power_models || metadata.power_models || {};
  const strategies = metadata.strategies || {};
  const showStrategies = shouldShowStrategyDetails(strategies, powers);
  els.powerDetails.replaceChildren(
    ...powers.map((power) => {
      const row = document.createElement("div");
      row.className = "power-row";
      row.classList.toggle("has-strategy", showStrategies);

      const swatch = document.createElement("span");
      swatch.className = "power-swatch";
      swatch.style.backgroundColor = powerColor(power);

      const name = document.createElement("strong");
      name.textContent = `${power} (${powerCode(power)})`;

      const model = document.createElement("span");
      model.className = "power-model";
      model.textContent = displayValue(models[power], "No model assigned");

      if (!showStrategies) {
        row.append(swatch, name, model);
        return row;
      }

      const assignment = document.createElement("span");
      assignment.className = "power-assignment";
      const strategy = document.createElement("span");
      strategy.className = "power-strategy";
      const strategySummary = strategyDisplay(strategies[power]);
      strategy.textContent = `${strategySummary.name}: ${strategySummary.description}`;
      assignment.append(model, strategy);
      row.append(swatch, name, assignment);
      return row;
    }),
  );
}

function shouldShowStrategyDetails(strategies, powers) {
  if (!strategies || typeof strategies !== "object") {
    return false;
  }
  return powers.some((power) => {
    const strategy = strategies[power];
    return !isBaselineStrategy(strategy?.name);
  });
}

function strategyDisplay(strategy) {
  const name = typeof strategy?.name === "string" ? strategy.name : "baseline";
  if (isBaselineStrategy(name)) {
    return {
      name: "No specific strategy",
      description: "Baseline play",
    };
  }
  return {
    name: titleFromIdentifier(name),
    description: "Custom strategy",
  };
}

function isBaselineStrategy(name) {
  return !name || name === "baseline" || name === "none";
}

function titleFromIdentifier(value) {
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function renderLiveEvents(events = state.liveEvents) {
  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = "No live events";
    els.liveEvents.replaceChildren(empty);
    return;
  }
  els.liveEvents.replaceChildren(...events.slice(-80).map(renderLiveEvent));
  els.liveEvents.scrollTop = els.liveEvents.scrollHeight;
}

function renderLiveEvent(event) {
  const item = document.createElement("article");
  item.className = "live-event";

  const title = document.createElement("strong");
  title.textContent = liveEventSummary(event);

  const meta = document.createElement("span");
  meta.textContent = liveEventMeta(event);

  const body = document.createElement("p");
  body.textContent = liveEventHint(event);

  item.append(title, meta, body);
  return item;
}

function liveEventSummary(event) {
  if (event.type === "run_started") {
    return "Run started";
  }
  if (event.type === "phase_started") {
    return `${displayValue(event.phase)} started`;
  }
  if (event.type === "message_sent") {
    if (isSystemConversationEvent(event)) {
      return "Conversation thread closed";
    }
    return `${powerCode(event.sender)} -> ${powerCode(event.recipient)} message sent`;
  }
  if (event.type === "orders_submitted") {
    return `${powerCode(event.power)} submitted orders`;
  }
  if (event.type === "reasoning_available") {
    return `${powerCode(event.power)} reasoning available`;
  }
  if (event.type === "phase_resolved") {
    return `${displayValue(event.resolved_phase, "Phase")} resolved`;
  }
  if (event.type === "year_summary") {
    const year = event.year == null ? "" : String(event.year);
    return year ? `Year ${year} summary` : "Year summary";
  }
  if (event.type === "game_finished") {
    return "Game finished";
  }
  if (event.type === "run_error") {
    return "Run stopped";
  }
  return String(event.type || "event").replaceAll("_", " ");
}

function liveEventMeta(event) {
  return [event.phase, event.status].filter(Boolean).join(" | ");
}

function liveEventHint(event) {
  if (event.type === "message_sent") {
    if (isSystemConversationEvent(event)) {
      return systemConversationText(event);
    }
    return "Open Messages to read the conversation.";
  }
  if (event.type === "orders_submitted") {
    return "Open Orders to inspect submitted orders.";
  }
  if (event.type === "reasoning_available") {
    return "Open Reasoning to inspect the model rationale.";
  }
  if (event.type === "phase_resolved") {
    return "Map and scores updated.";
  }
  if (event.type === "run_error") {
    return "Check the server output or run files for details.";
  }
  if (event.type === "game_finished") {
    return event.winner ? `Winner: ${event.winner}` : "Completed without a winner.";
  }
  return "";
}

function updateStatusFromEvent(event) {
  if (event.type === "run_error") {
    setStatus("error");
    return;
  }
  if (event.type === "game_finished") {
    setStatus("done");
    return;
  }
  setStatus(statusLabel(event.status, event.phase || state.liveRunId || ""));
  updateRunningBanner(event);
}

function statusLabel(status, detail) {
  return [status || "running", detail].filter(Boolean).join(" - ");
}

function showLauncher() {
  placeMusicPlayerInLauncher();
  stopPlayback();
  stopLiveReplayRefresh();
  if (state.liveSource) {
    state.liveSource.close();
    state.liveSource = null;
  }
  state.liveRunId = null;
  state.liveRunMode = null;
  state.liveRunActive = false;
  state.liveLaunchPending = false;
  state.followLiveTail = false;
  state.liveEvents = [];
  state.replay = null;
  state.frameIndex = 0;
  resetConversationIndicators();
  state.trackUnreadConversations = false;
  els.apiKeyInput.value = "";
  els.dashboardView.classList.add("is-hidden");
  els.launcherView.classList.remove("is-hidden");
  els.runStateBanner.hidden = true;
  els.postRunActions.hidden = true;
  setLaunchEnabled(true);
  syncLaunchMode();
  updateTimelineState();
  clearLauncherWarning();
  setLauncherStatus(state.setups.length ? "Choose a setup to start" : "Loading setups");
}

function showDashboardRunning(mode) {
  placeMusicPlayerInDashboard();
  els.launcherView.classList.add("is-hidden");
  els.dashboardView.classList.remove("is-hidden");
  els.runStateBanner.hidden = false;
  els.postRunActions.hidden = true;
  state.liveRunMode = mode;
  state.liveRunActive = true;
  state.followLiveTail = true;
  resetConversationIndicators();
  state.trackUnreadConversations = true;
  updateRunningBanner();
  setControlsEnabled(false);
  updateTimelineState();
  clearLauncherWarning();
}

function showDashboardFinished() {
  placeMusicPlayerInDashboard();
  els.launcherView.classList.add("is-hidden");
  els.dashboardView.classList.remove("is-hidden");
  els.runStateBanner.hidden = true;
  els.postRunActions.hidden = false;
  state.liveRunActive = false;
  state.followLiveTail = false;
  updateTimelineState();
}

function updateRunningBanner(event = null) {
  const modeLabel = state.liveRunMode === "live" ? "Live run" : "Run";
  const detail = event?.phase || event?.status || state.liveRunId || "starting";
  els.runStateText.textContent = `${modeLabel} in progress - ${detail}`;
}

function showFrame(nextIndex, options = {}) {
  if (!state.replay || !state.replay.frames.length) {
    return;
  }
  const maxIndex = state.replay.frames.length - 1;
  state.frameIndex = Math.min(Math.max(nextIndex, 0), maxIndex);
  if (state.liveRunActive) {
    if (options.followLiveTail) {
      state.followLiveTail = true;
    } else if (options.userSelected || state.frameIndex === maxIndex) {
      state.followLiveTail = state.frameIndex === maxIndex;
    }
  }
  const frame = state.replay.frames[state.frameIndex];
  renderSvg(frame.svg);
  renderReplayEvents(frame);
  els.phaseName.textContent = frame.phase || "-";
  els.phaseIndex.textContent = phaseIndexLabel(frame, state.frameIndex, state.replay.frames.length);
  els.phaseSlider.value = String(state.frameIndex);
  els.prevBtn.disabled = state.frameIndex === 0;
  els.nextBtn.disabled = state.frameIndex === maxIndex;
  if (state.frameIndex === maxIndex) {
    stopPlayback();
  }
  updateTimelineState();
}

function phaseIndexLabel(frame, index, total) {
  const displayTotal = state.replay?.hasPrelude ? Math.max(0, total - 1) : total;
  if (frame?.isPrelude) {
    return `0 / ${displayTotal}`;
  }
  const phaseNumber = state.replay?.hasPrelude ? index : index + 1;
  return `${phaseNumber} / ${displayTotal}`;
}

function jumpToLiveTail() {
  if (!state.replay || !state.replay.frames.length) {
    return;
  }
  state.followLiveTail = true;
  showFrame(state.replay.frames.length - 1, {followLiveTail: true});
}

function renderSvg(svgText) {
  const documentSvg = new DOMParser().parseFromString(svgText, "image/svg+xml");
  documentSvg.querySelectorAll("script").forEach((script) => script.remove());
  els.boardFrame.replaceChildren(document.importNode(documentSvg.documentElement, true));
}

function renderEmptyBoard(text) {
  const empty = document.createElement("div");
  empty.className = "empty-board";
  empty.textContent = text;
  els.boardFrame.replaceChildren(empty);
  els.phaseName.textContent = "-";
  els.phaseIndex.textContent = "0 / 0";
  updateTimelineState();
}

function renderReplayEvents(frame) {
  const visibleEvents = eventsUpToFrame(frame);
  const orderEvents = latestOrdersUpToFrame(visibleEvents);
  if (!state.liveRunActive) {
    renderLiveEvents(visibleEvents);
  }
  renderConversations(visibleEvents.filter((event) => event.type === "message_sent"));
  renderPhaseDetails(
    orderEvents,
    reasoningEventsForOrders(orderEvents, visibleEvents),
    frame,
    visibleEvents,
  );
}

function eventsUpToFrame(frame) {
  if (frame?.isPrelude) {
    return [];
  }
  const events = Array.isArray(state.replay?.events) ? state.replay.events : [];
  const currentIndex = eventPhaseIndex(frame);
  return events
    .filter((event) => {
      const index = eventPhaseIndex(event);
      return index !== null && currentIndex !== null && index <= currentIndex;
    })
    .sort(eventSort);
}

function eventPhaseIndex(event) {
  if (typeof event.phase_index === "number") {
    return event.phase_index;
  }
  if (!state.replay || typeof event.phase !== "string") {
    return null;
  }
  const frame = state.replay.frames.find((candidate) => candidate.phase === event.phase);
  return frame ? frame.phase_index : null;
}

function eventSort(left, right) {
  const leftSequence = typeof left.sequence === "number" ? left.sequence : left.event_index;
  const rightSequence = typeof right.sequence === "number" ? right.sequence : right.event_index;
  return leftSequence - rightSequence;
}

function latestOrdersUpToFrame(events) {
  const orderEvents = events.filter(
    (event) =>
      event.type === "orders_submitted" &&
      Array.isArray(event.orders) &&
      event.orders.length > 0,
  );
  if (!orderEvents.length) {
    return [];
  }
  const phaseIndexes = orderEvents
    .map((event) => eventPhaseIndex(event))
    .filter((index) => index !== null);
  if (!phaseIndexes.length) {
    return [];
  }
  const latestPhaseIndex = Math.max(...phaseIndexes);
  return orderEvents.filter((event) => eventPhaseIndex(event) === latestPhaseIndex);
}

function reasoningEventsForOrders(orderEvents, events) {
  if (!orderEvents.length) {
    return [];
  }
  const reasoningEvents = events.filter((event) => event.type === "reasoning_available");
  return orderEvents.flatMap((orderEvent) => {
    const match = reasoningEvents
      .filter(
        (event) =>
          event.power === orderEvent.power &&
          eventPhaseIndex(event) === eventPhaseIndex(orderEvent),
      )
      .sort(eventSort)
      .at(-1);
    if (match) {
      return [match];
    }
    if (
      !orderEvent.is_fallback ||
      !Array.isArray(orderEvent.orders) ||
      !orderEvent.orders.length
    ) {
      return [];
    }
    return {
      type: "reasoning_available",
      phase: orderEvent.phase,
      phase_index: orderEvent.phase_index,
      phase_type: orderEvent.phase_type,
      power: orderEvent.power,
      reasoning: "",
      missing_reasoning: true,
    };
  });
}

function renderConversations(messages) {
  const conversations = groupConversations(messages);
  if (!conversations.length) {
    state.activeConversationKey = null;
    renderEmptyMessages("No conversations yet");
    return;
  }

  if (!conversations.some((conversation) => conversation.key === state.activeConversationKey)) {
    state.activeConversationKey = conversations[0].key;
  }
  updateUnreadConversations(conversations);

  els.conversationTabs.replaceChildren(
    ...conversations.map((conversation) => renderConversationTab(conversation)),
  );

  const activeConversation = conversations.find(
    (conversation) => conversation.key === state.activeConversationKey,
  );
  renderChatThread(activeConversation || conversations[0]);
}

function groupConversations(messages) {
  const byKey = new Map();
  for (const message of messages) {
    const participants = [message.sender, message.recipient].filter(Boolean).sort();
    if (participants.length !== 2) {
      continue;
    }
    const key = participants.join("__");
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        participants,
        label: participants.map(powerCode).join(" - "),
        messages: [],
        diplomaticMessageCount: 0,
      });
    }
    const conversation = byKey.get(key);
    conversation.messages.push(message);
    if (!isSystemConversationEvent(message)) {
      conversation.diplomaticMessageCount += 1;
    }
  }
  return [...byKey.values()].sort((left, right) => left.label.localeCompare(right.label));
}

function renderConversationTab(conversation) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "conversation-tab";
  if (conversation.key === state.activeConversationKey) {
    button.classList.add("active");
  }
  if (state.unreadConversationKeys.has(conversation.key)) {
    button.classList.add("has-unread");
  }
  button.textContent = `${conversation.label} (${conversation.diplomaticMessageCount})`;
  button.addEventListener("click", () => {
    state.activeConversationKey = conversation.key;
    state.unreadConversationKeys.delete(conversation.key);
    state.conversationActivityCounts.set(conversation.key, conversation.messages.length);
    renderConversations(
      eventsUpToFrame(state.replay.frames[state.frameIndex]).filter(
        (event) => event.type === "message_sent",
      ),
    );
  });
  return button;
}

function renderChatThread(conversation) {
  const participantA = conversation.participants[0];
  const previousConversationKey = els.messageList.dataset.conversationKey || "";
  const isSameConversation = previousConversationKey === conversation.key;
  const shouldScrollToBottom = !isSameConversation || isChatNearBottom();
  const previousScrollTop = els.messageList.scrollTop;
  const items = [];
  let lastPhase = null;
  for (const message of conversation.messages) {
    if (message.phase !== lastPhase) {
      items.push(renderPhaseDivider(message.phase));
      lastPhase = message.phase;
    }
    items.push(renderChatMessage(message, participantA));
  }
  els.messageList.replaceChildren(...items);
  els.messageList.dataset.conversationKey = conversation.key;
  if (shouldScrollToBottom) {
    els.messageList.scrollTop = els.messageList.scrollHeight;
  } else {
    els.messageList.scrollTop = previousScrollTop;
  }
}

function isChatNearBottom() {
  const distanceFromBottom =
    els.messageList.scrollHeight - els.messageList.scrollTop - els.messageList.clientHeight;
  return distanceFromBottom < 28;
}

function updateUnreadConversations(conversations) {
  if (!state.trackUnreadConversations) {
    for (const conversation of conversations) {
      state.conversationActivityCounts.set(conversation.key, conversation.messages.length);
    }
    state.unreadConversationKeys.clear();
    return;
  }

  for (const conversation of conversations) {
    const previousCount = state.conversationActivityCounts.get(conversation.key) || 0;
    if (
      conversation.messages.length > previousCount &&
      conversation.key !== state.activeConversationKey
    ) {
      state.unreadConversationKeys.add(conversation.key);
    }
    state.conversationActivityCounts.set(conversation.key, conversation.messages.length);
  }
  if (state.activeConversationKey) {
    state.unreadConversationKeys.delete(state.activeConversationKey);
  }
}

function resetConversationIndicators() {
  state.conversationActivityCounts.clear();
  state.unreadConversationKeys.clear();
  delete els.messageList.dataset.conversationKey;
}

function renderPhaseDivider(phase) {
  const divider = document.createElement("div");
  divider.className = "phase-divider";
  divider.textContent = displayValue(phase, "Phase");
  return divider;
}

function renderChatMessage(message, participantA) {
  if (isSystemConversationEvent(message)) {
    return renderSystemChatNotice(message);
  }

  const bubble = document.createElement("article");
  bubble.className = "chat-message";
  bubble.classList.add(message.sender === participantA ? "from-a" : "from-b");

  const meta = document.createElement("div");
  meta.className = "chat-meta";
  meta.textContent = `${powerCode(message.sender)} | ${displayValue(message.phase)}`;

  const body = document.createElement("p");
  body.className = "chat-body";
  body.textContent = displayValue(message.body || message.message, "");

  bubble.append(meta, body);
  return bubble;
}

function renderSystemChatNotice(message) {
  const notice = document.createElement("article");
  notice.className = "chat-system-message";

  const body = document.createElement("p");
  body.textContent = systemConversationText(message);

  notice.append(body);
  return notice;
}

function isSystemConversationEvent(message) {
  const body = displayValue(message.body || message.message, "");
  return Boolean(message.system_event) || body.toLowerCase().startsWith("[system]");
}

function systemConversationText(message) {
  const sender = displayValue(message.sender);
  const recipient = displayValue(message.recipient);
  if (message.system_event === "thread_closed") {
    if (message.system_reason === "message_limit_reached") {
      return `${sender} had no message slot left to respond to ${recipient}. Thread closed for this phase.`;
    }
    if (message.system_reason === "reply_declined") {
      return `${sender} chose not to respond to ${recipient}. Thread closed for this phase.`;
    }
  }

  const raw = displayValue(message.body || message.message, "");
  return raw
    .replace(/^\[system\]\s*/i, "")
    .replace("declined to respond to", "chose not to respond to");
}

function renderEmptyMessages(text) {
  els.conversationTabs.replaceChildren();
  const empty = document.createElement("p");
  empty.className = "empty-list";
  empty.textContent = text;
  els.messageList.replaceChildren(empty);
}

function renderPhaseDetails(orderEvents, reasoningEvents, frame = null, visibleEvents = []) {
  renderOrders(orderEvents, frame);
  renderReasoning(reasoningEvents, frame);
  renderStats(visibleEvents, frame);
  setDetailTab(state.activeDetailTab);
}

function renderOrders(events, frame) {
  if (!events.length) {
    renderEmptyPane(
      els.ordersPane,
      frame?.isPrelude ? "No orders yet" : "No orders for this phase",
    );
    return;
  }
  els.ordersPane.replaceChildren(
    renderOrdersToolbar(events, frame),
    ...events.map(renderOrderBlock),
  );
}

function renderOrdersToolbar(events, frame) {
  const toolbar = document.createElement("div");
  toolbar.className = "orders-toolbar";

  const title = document.createElement("p");
  title.className = "orders-source";
  const sourcePhase = events[0]?.phase || "-";
  const currentPhase = frame?.phase;
  title.textContent =
    currentPhase && currentPhase !== sourcePhase
      ? `Showing latest orders from ${sourcePhase}`
      : `Orders for ${sourcePhase}`;

  const infoWrapper = document.createElement("div");
  infoWrapper.className = "orders-info";

  const infoButton = document.createElement("button");
  infoButton.className = "info-button";
  infoButton.type = "button";
  infoButton.setAttribute("aria-label", "Order legend");
  infoButton.textContent = "i";

  const tooltip = document.createElement("div");
  tooltip.className = "orders-tooltip";
  tooltip.setAttribute("role", "tooltip");
  const legend = document.createElement("ul");
  legend.replaceChildren(
    ...[
      ["Hold", "Stay in place."],
      ["Move", "Go to another province."],
      ["Support", "Help another unit hold or move."],
      ["Convoy", "Fleet transports an army by sea."],
      ["Retreat", "Displaced unit moves away."],
      ["Build", "Create a new unit."],
      ["Disband", "Remove a unit."],
      ["Waive", "Skip a build."],
    ].map(([label, description]) => {
      const item = document.createElement("li");
      const strong = document.createElement("strong");
      strong.textContent = `${label}: `;
      item.append(strong, description);
      return item;
    }),
  );
  tooltip.appendChild(legend);

  infoWrapper.append(infoButton, tooltip);
  toolbar.append(title, infoWrapper);
  return toolbar;
}

function renderOrderBlock(event) {
  const block = document.createElement("article");
  block.className = "detail-block";

  const title = document.createElement("h3");
  title.textContent = displayValue(event.power, "Power");

  const list = document.createElement("ul");
  list.className = "orders-list";
  list.replaceChildren(
    ...event.orders.map((order) => {
      const entry = document.createElement("li");
      const readable = document.createElement("span");
      readable.className = "order-readable";
      readable.textContent = readableOrder(order);
      const code = document.createElement("span");
      code.className = "order-code";
      code.textContent = order;
      entry.append(readable, code);
      return entry;
    }),
  );
  block.append(title, list);
  return block;
}

function readableOrder(order) {
  const normalized = order.trim().replace(/\s+/g, " ");
  if (normalized === "WAIVE") {
    return "Waive this build.";
  }

  const parts = normalized.split(" ");
  if (parts.length < 3) {
    return normalized;
  }

  const unit = unitName(parts[0]);
  const source = locationName(parts[1]);
  const action = parts[2];

  if (action === "H") {
    return `${unit} in ${source} holds.`;
  }
  if (action === "-") {
    const target = locationName(parts[3]);
    const viaConvoy = parts.includes("VIA") ? " by convoy" : "";
    return `${unit} in ${source} moves to ${target}${viaConvoy}.`;
  }
  if (action === "S") {
    return readableSupportOrder(unit, source, parts);
  }
  if (action === "C") {
    return readableConvoyOrder(unit, source, parts);
  }
  if (action === "R") {
    return `${unit} in ${source} retreats to ${locationName(parts[3])}.`;
  }
  if (action === "D") {
    return `${unit} in ${source} disbands.`;
  }
  if (action === "B") {
    return `Build ${unit.toLowerCase()} in ${source}.`;
  }
  return normalized;
}

function readableSupportOrder(unit, source, parts) {
  const supportedUnit = unitName(parts[3]);
  const supportedSource = locationName(parts[4]);
  const moveMarkerIndex = parts.indexOf("-");
  if (moveMarkerIndex >= 0 && moveMarkerIndex + 1 < parts.length) {
    const target = locationName(parts[moveMarkerIndex + 1]);
    return `${unit} in ${source} supports ${supportedUnit.toLowerCase()} in ${supportedSource} moving to ${target}.`;
  }
  return `${unit} in ${source} supports ${supportedUnit.toLowerCase()} in ${supportedSource} to hold.`;
}

function readableConvoyOrder(unit, source, parts) {
  const convoyedUnit = unitName(parts[3]);
  const convoyedSource = locationName(parts[4]);
  const moveMarkerIndex = parts.indexOf("-");
  const target =
    moveMarkerIndex >= 0 && moveMarkerIndex + 1 < parts.length
      ? locationName(parts[moveMarkerIndex + 1])
      : "another province";
  return `${unit} in ${source} convoys ${convoyedUnit.toLowerCase()} in ${convoyedSource} to ${target}.`;
}

function unitName(code) {
  if (code === "A") {
    return "Army";
  }
  if (code === "F") {
    return "Fleet";
  }
  return code;
}

function locationName(code) {
  if (!code) {
    return "-";
  }
  const [base, coast] = code.split("/");
  const name = LOCATION_NAMES[base] || base;
  if (!coast) {
    return name;
  }
  const coastName = {
    NC: "north coast",
    SC: "south coast",
    EC: "east coast",
    WC: "west coast",
  }[coast];
  return coastName ? `${name} (${coastName})` : `${name} (${coast})`;
}

const POWER_CODES = {
  AUSTRIA: "AUS",
  ENGLAND: "ENG",
  FRANCE: "FRA",
  GERMANY: "GER",
  ITALY: "ITA",
  RUSSIA: "RUS",
  TURKEY: "TUR",
};

const POWER_COLORS = {
  AUSTRIA: "#c48f85",
  ENGLAND: "darkviolet",
  FRANCE: "royalblue",
  GERMANY: "#a08a75",
  ITALY: "forestgreen",
  RUSSIA: "#757d91",
  TURKEY: "#b9a61c",
};

function powerCode(power) {
  return POWER_CODES[power] || String(power || "").slice(0, 3).toUpperCase();
}

function powerColor(power) {
  return POWER_COLORS[power] || "#8b98a5";
}

const LOCATION_NAMES = {
  ADR: "Adriatic Sea",
  AEG: "Aegean Sea",
  ALB: "Albania",
  ANK: "Ankara",
  APU: "Apulia",
  ARM: "Armenia",
  BAL: "Baltic Sea",
  BAR: "Barents Sea",
  BEL: "Belgium",
  BER: "Berlin",
  BLA: "Black Sea",
  BOH: "Bohemia",
  BOT: "Gulf of Bothnia",
  BRE: "Brest",
  BUD: "Budapest",
  BUL: "Bulgaria",
  BUR: "Burgundy",
  CLY: "Clyde",
  CON: "Constantinople",
  DEN: "Denmark",
  EAS: "Eastern Mediterranean",
  EDI: "Edinburgh",
  ENG: "English Channel",
  FIN: "Finland",
  GAL: "Galicia",
  GAS: "Gascony",
  GOB: "Gulf of Bothnia",
  GOL: "Gulf of Lyon",
  GRE: "Greece",
  HEL: "Helgoland Bight",
  HOL: "Holland",
  ION: "Ionian Sea",
  IRI: "Irish Sea",
  KIE: "Kiel",
  LON: "London",
  LVN: "Livonia",
  LVP: "Liverpool",
  MAO: "Mid-Atlantic Ocean",
  MAR: "Marseilles",
  MOS: "Moscow",
  MUN: "Munich",
  NAF: "North Africa",
  NAP: "Naples",
  NAT: "North Atlantic Ocean",
  NTH: "North Sea",
  NWG: "Norwegian Sea",
  NWY: "Norway",
  PAR: "Paris",
  PIC: "Picardy",
  PIE: "Piedmont",
  POR: "Portugal",
  PRU: "Prussia",
  ROM: "Rome",
  RUH: "Ruhr",
  RUM: "Rumania",
  SER: "Serbia",
  SEV: "Sevastopol",
  SIL: "Silesia",
  SKA: "Skagerrak",
  SMY: "Smyrna",
  SPA: "Spain",
  STP: "St. Petersburg",
  SWE: "Sweden",
  SYR: "Syria",
  TRI: "Trieste",
  TUN: "Tunis",
  TUS: "Tuscany",
  TYR: "Tyrolia",
  TYS: "Tyrrhenian Sea",
  UKR: "Ukraine",
  VEN: "Venice",
  VIE: "Vienna",
  WAL: "Wales",
  WAR: "Warsaw",
  WES: "Western Mediterranean",
  YOR: "Yorkshire",
};

function renderStats(visibleEvents = [], frame = null) {
  const previousScrollTop = els.statsPane.scrollTop;
  if (frame?.isPrelude) {
    renderEmptyPane(els.statsPane, "No stats yet");
    return;
  }
  const metadata = state.replay?.metadata || {};
  const powers = statPowers(metadata, visibleEvents);
  if (!powers.length) {
    renderEmptyPane(els.statsPane, "No stats available for this run");
    return;
  }

  const selectedPhaseIndex = selectedStatsPhaseIndex(frame);
  const {startYear, maxYear} = supplyYearRange(metadata, visibleEvents);
  const winScore = positiveNumber(metadata.win_score, maxObservedScore(visibleEvents, powers, 1));
  const latestScores = latestScoresUpToPhase(visibleEvents, powers, selectedPhaseIndex);
  const orderStats = cumulativeOrderStats(visibleEvents, powers);

  const layout = document.createElement("div");
  layout.className = "stats-layout";

  const trajectoryCard = document.createElement("section");
  trajectoryCard.className = "stats-card stats-card-wide";
  trajectoryCard.appendChild(
    renderSupplyCenterChart({
      events: visibleEvents,
      powers,
      selectedPhaseIndex,
      startYear,
      maxYear,
      winScore,
    }),
  );

  layout.appendChild(
    renderScoreSummary({
      powers,
      scores: latestScores,
      phase: frame?.phase || "",
      winScore,
    }),
  );

  const orderGrid = document.createElement("div");
  orderGrid.className = "order-stats-grid";
  orderGrid.replaceChildren(
    ...ORDER_STAT_TYPES.map((stat) =>
      renderOrderStatChart({
        stat,
        powers,
        counts: orderStats[stat.key],
      }),
    ),
  );

  layout.append(trajectoryCard, orderGrid);
  els.statsPane.replaceChildren(layout);
  if (state.activeDetailTab === "stats") {
    els.statsPane.scrollTop = Math.min(
      previousScrollTop,
      Math.max(0, els.statsPane.scrollHeight - els.statsPane.clientHeight),
    );
  }
}

function statPowers(metadata, events) {
  const metadataPowers = Array.isArray(metadata.powers) ? metadata.powers : [];
  if (metadataPowers.length) {
    return metadataPowers.map(String);
  }

  const powers = new Set();
  for (const event of events) {
    if (event.scores && typeof event.scores === "object") {
      Object.keys(event.scores).forEach((power) => powers.add(power));
    }
    if (event.type === "orders_submitted" && event.power) {
      powers.add(String(event.power));
    }
  }
  return [...powers].sort();
}

function selectedStatsPhaseIndex(frame) {
  const index = frame ? eventPhaseIndex(frame) : null;
  return typeof index === "number" ? index : state.frameIndex;
}

function positiveNumber(value, fallback) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue > 0 ? numberValue : fallback;
}

function supplyYearRange(metadata, events) {
  const observedYears = events
    .map(scoreEventYear)
    .filter((year) => typeof year === "number");
  const startYear = observedYears.length ? Math.min(...observedYears) : 1901;
  const maxYears = positiveNumber(metadata.max_years, 0);
  if (maxYears > 0) {
    return {
      startYear,
      maxYear: startYear + Math.max(0, Math.round(maxYears) - 1),
    };
  }
  return {
    startYear,
    maxYear: Math.max(startYear, ...observedYears),
  };
}

function maxObservedScore(events, powers, fallback) {
  let highest = fallback;
  for (const event of events) {
    if (!event.scores || typeof event.scores !== "object") {
      continue;
    }
    for (const power of powers) {
      const value = Number(event.scores[power]);
      if (Number.isFinite(value)) {
        highest = Math.max(highest, value);
      }
    }
  }
  return highest;
}

function latestScoresUpToPhase(events, powers, selectedPhaseIndex) {
  const scoreEvents = events
    .filter((event) => event.scores && typeof event.scores === "object")
    .filter((event) => {
      const phaseIndex = eventPhaseIndex(event);
      return typeof phaseIndex === "number" && phaseIndex <= selectedPhaseIndex;
    })
    .sort(eventSort);
  const latest = scoreEvents.at(-1)?.scores || {};
  return Object.fromEntries(
    powers.map((power) => {
      const value = Number(latest[power]);
      return [power, Number.isFinite(value) ? value : null];
    }),
  );
}

function renderScoreSummary({powers, scores, phase, winScore}) {
  const summary = document.createElement("section");
  summary.className = "score-summary";

  const title = document.createElement("div");
  title.className = "score-summary-title";
  const phaseLabel = phase ? ` at ${phase}` : "";
  title.textContent = `Supply centers${phaseLabel}`;

  const target = document.createElement("span");
  target.className = "score-target";
  target.textContent = `Win target ${winScore}`;
  title.appendChild(target);

  const list = document.createElement("div");
  list.className = "score-chips";
  list.replaceChildren(
    ...powers.map((power) => {
      const chip = document.createElement("span");
      chip.className = "score-chip";

      const swatch = document.createElement("span");
      swatch.className = "score-swatch";
      swatch.style.backgroundColor = powerColor(power);

      const label = document.createElement("strong");
      label.textContent = powerCode(power);

      const value = document.createElement("span");
      const scoreValue = scores[power];
      value.textContent = scoreValue === null ? "-" : String(scoreValue);

      chip.append(swatch, label, value);
      return chip;
    }),
  );

  summary.append(title, list);
  return summary;
}

function renderSupplyCenterChart({
  events,
  powers,
  selectedPhaseIndex,
  startYear,
  maxYear,
  winScore,
}) {
  const width = 820;
  const height = 300;
  const margin = {top: 34, right: 124, bottom: 42, left: 50};
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const svg = chartSvg(width, height, "stats-chart supply-chart");

  appendChartTitle(svg, width, "Supply center trajectory by nation");
  appendGridAndAxes(svg, {
    width,
    height,
    margin,
    plotWidth,
    plotHeight,
    yMax: winScore,
    xMin: startYear,
    xMax: maxYear,
    yLabel: "Supply centers",
    xLabel: "Year",
  });

  const timelines = supplyTimelines(events, powers, selectedPhaseIndex);
  for (const power of powers) {
    const points = timelines.get(power) || [];
    const plotted = points.map((point) => ({
      x: xScale(point.year, startYear, maxYear, margin.left, plotWidth),
      y: yScale(point.value, winScore, margin.top, plotHeight),
    }));
    if (plotted.length > 1) {
      svg.appendChild(
        svgEl("polyline", {
          class: "chart-line",
          points: plotted.map((point) => `${point.x},${point.y}`).join(" "),
          stroke: powerColor(power),
        }),
      );
    }
    for (const point of plotted) {
      svg.appendChild(
        svgEl("circle", {
          class: "chart-point",
          cx: point.x,
          cy: point.y,
          r: 4,
          fill: powerColor(power),
        }),
      );
    }
  }

  appendLegend(svg, powers, width - margin.right + 24, margin.top + 2);
  return svg;
}

function supplyTimelines(events, powers, selectedPhaseIndex) {
  const timelines = new Map(powers.map((power) => [power, []]));
  const scoreEvents = events
    .filter((event) => event.scores && typeof event.scores === "object")
    .sort(eventSort);

  for (const event of scoreEvents) {
    const phaseIndex = eventPhaseIndex(event);
    const year = scoreEventYear(event);
    if (typeof phaseIndex !== "number" || phaseIndex > selectedPhaseIndex) {
      continue;
    }
    if (typeof year !== "number") {
      continue;
    }
    for (const power of powers) {
      const value = Number(event.scores[power]);
      if (!Number.isFinite(value)) {
        continue;
      }
      upsertTimelinePoint(timelines.get(power), {
        year,
        value,
      });
    }
  }
  return timelines;
}

function scoreEventYear(event) {
  const calendarYear = Number(event.calendar_year);
  if (Number.isInteger(calendarYear)) {
    return calendarYear;
  }
  const resolvedPhaseYear = phaseYear(event.resolved_phase);
  if (resolvedPhaseYear !== null) {
    return resolvedPhaseYear;
  }
  if (event.type === "run_started") {
    return phaseYear(event.phase);
  }
  return null;
}

function phaseYear(phase) {
  if (typeof phase !== "string" || phase.length < 5) {
    return null;
  }
  const year = Number(phase.slice(1, 5));
  return Number.isInteger(year) ? year : null;
}

function upsertTimelinePoint(points, point) {
  if (!points) {
    return;
  }
  const last = points.at(-1);
  if (last && last.year === point.year) {
    points[points.length - 1] = point;
    return;
  }
  points.push(point);
}

function cumulativeOrderStats(events, powers) {
  const stats = Object.fromEntries(
    ORDER_STAT_TYPES.map((stat) => [
      stat.key,
      Object.fromEntries(powers.map((power) => [power, 0])),
    ]),
  );

  for (const event of events) {
    if (
      event.type !== "orders_submitted" ||
      !event.power ||
      !Array.isArray(event.orders)
    ) {
      continue;
    }
    const power = String(event.power);
    if (!powers.includes(power)) {
      continue;
    }
    for (const order of event.orders) {
      const orderType = orderStatType(order);
      if (orderType && stats[orderType]) {
        stats[orderType][power] += 1;
      }
    }
  }
  return stats;
}

function orderStatType(order) {
  const parts = String(order || "").trim().split(/\s+/);
  const action = parts[2];
  if (action === "S") {
    return "support";
  }
  if (action === "R") {
    return "retreat";
  }
  if (action === "H") {
    return "hold";
  }
  if (action === "-") {
    return "move";
  }
  return null;
}

function renderOrderStatChart({stat, powers, counts}) {
  const card = document.createElement("section");
  card.className = "stats-card order-stat-card";

  const width = 360;
  const height = 210;
  const margin = {top: 34, right: 16, bottom: 44, left: 38};
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const values = powers.map((power) => counts?.[power] || 0);
  const yMax = Math.max(1, ...values);
  const svg = chartSvg(width, height, "stats-chart order-chart");

  appendChartTitle(svg, width, stat.title);
  appendBarGrid(svg, {width, height, margin, plotWidth, plotHeight, yMax});

  const slotWidth = plotWidth / Math.max(1, powers.length);
  const barWidth = Math.min(34, slotWidth * 0.55);
  powers.forEach((power, index) => {
    const value = counts?.[power] || 0;
    const barHeight = (value / yMax) * plotHeight;
    const x = margin.left + slotWidth * index + (slotWidth - barWidth) / 2;
    const y = margin.top + plotHeight - barHeight;

    svg.appendChild(
      svgEl("rect", {
        class: "chart-bar",
        x,
        y,
        width: barWidth,
        height: barHeight,
        fill: powerColor(power),
      }),
    );
    if (value > 0) {
      svg.appendChild(
        svgEl(
          "text",
          {
            class: "chart-value",
            x: x + barWidth / 2,
            y: Math.max(margin.top + 10, y - 5),
            "text-anchor": "middle",
          },
          String(value),
        ),
      );
    }
    svg.appendChild(
      svgEl(
        "text",
        {
          class: "chart-tick-label chart-x-label",
          x: margin.left + slotWidth * index + slotWidth / 2,
          y: height - 16,
          "text-anchor": "middle",
        },
        powerCode(power),
      ),
    );
  });

  if (values.every((value) => value === 0)) {
    svg.appendChild(
      svgEl(
        "text",
        {
          class: "chart-empty-label",
          x: margin.left + plotWidth / 2,
          y: margin.top + plotHeight / 2,
          "text-anchor": "middle",
        },
        stat.empty,
      ),
    );
  }

  card.appendChild(svg);
  return card;
}

function chartSvg(width, height, className) {
  return svgEl("svg", {
    class: className,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-hidden": "true",
  });
}

function appendChartTitle(svg, width, title) {
  svg.appendChild(
    svgEl(
      "text",
      {
        class: "chart-title",
        x: width / 2,
        y: 20,
        "text-anchor": "middle",
      },
      title,
    ),
  );
}

function appendGridAndAxes(svg, options) {
  const {height, margin, plotWidth, plotHeight, yMax, xMax, yLabel, xLabel} = options;
  const xMin = options.xMin ?? 0;
  appendBarGrid(svg, options);
  const xTicks = chartTicksBetween(xMin, xMax, 6);
  for (const value of xTicks) {
    const x = xScale(value, xMin, xMax, margin.left, plotWidth);
    svg.appendChild(
      svgEl("line", {
        class: "chart-grid-line",
        x1: x,
        x2: x,
        y1: margin.top,
        y2: margin.top + plotHeight,
      }),
    );
    svg.appendChild(
      svgEl(
        "text",
        {
          class: "chart-tick-label",
          x,
          y: height - 20,
          "text-anchor": "middle",
        },
        String(value),
      ),
    );
  }
  svg.appendChild(
    svgEl(
      "text",
      {
        class: "chart-axis-label",
        x: margin.left + plotWidth / 2,
        y: height - 5,
        "text-anchor": "middle",
      },
      xLabel,
    ),
  );
  svg.appendChild(
    svgEl(
      "text",
      {
        class: "chart-axis-label",
        x: 14,
        y: margin.top + plotHeight / 2,
        transform: `rotate(-90 14 ${margin.top + plotHeight / 2})`,
        "text-anchor": "middle",
      },
      yLabel,
    ),
  );
}

function appendBarGrid(svg, {margin, plotWidth, plotHeight, yMax}) {
  svg.appendChild(
    svgEl("line", {
      class: "chart-axis-line",
      x1: margin.left,
      x2: margin.left,
      y1: margin.top,
      y2: margin.top + plotHeight,
    }),
  );
  svg.appendChild(
    svgEl("line", {
      class: "chart-axis-line",
      x1: margin.left,
      x2: margin.left + plotWidth,
      y1: margin.top + plotHeight,
      y2: margin.top + plotHeight,
    }),
  );
  for (const value of chartTicks(yMax, 4)) {
    const y = yScale(value, yMax, margin.top, plotHeight);
    svg.appendChild(
      svgEl("line", {
        class: "chart-grid-line",
        x1: margin.left,
        x2: margin.left + plotWidth,
        y1: y,
        y2: y,
      }),
    );
    svg.appendChild(
      svgEl(
        "text",
        {
          class: "chart-tick-label",
          x: margin.left - 8,
          y: y + 4,
          "text-anchor": "end",
        },
        String(value),
      ),
    );
  }
}

function chartTicks(maxValue, targetCount) {
  const max = Math.max(1, Math.round(maxValue));
  const step = Math.max(1, Math.ceil(max / targetCount));
  const ticks = [];
  for (let value = 0; value < max; value += step) {
    ticks.push(value);
  }
  if (!ticks.includes(max)) {
    ticks.push(max);
  }
  return ticks;
}

function chartTicksBetween(minValue, maxValue, targetCount) {
  const min = Math.round(minValue);
  const max = Math.max(min, Math.round(maxValue));
  const span = max - min;
  if (span <= 0) {
    return [min];
  }
  if (span <= 12) {
    return Array.from({length: span + 1}, (_, index) => min + index);
  }
  const step = Math.max(1, Math.ceil(span / targetCount));
  const ticks = [];
  for (let value = min; value < max; value += step) {
    ticks.push(value);
  }
  if (!ticks.includes(max)) {
    ticks.push(max);
  }
  return ticks;
}

function appendLegend(svg, powers, x, y) {
  powers.forEach((power, index) => {
    const rowY = y + index * 18;
    svg.appendChild(
      svgEl("circle", {
        cx: x,
        cy: rowY,
        r: 5,
        fill: powerColor(power),
      }),
    );
    svg.appendChild(
      svgEl(
        "text",
        {
          class: "chart-legend-label",
          x: x + 12,
          y: rowY + 4,
        },
        powerCode(power),
      ),
    );
  });
}

function xScale(value, minValue, maxValue, left, width) {
  const span = Math.max(1, maxValue - minValue);
  const clamped = Math.max(minValue, Math.min(value, maxValue));
  return left + ((clamped - minValue) / span) * width;
}

function yScale(value, maxValue, top, height) {
  return top + height - (Math.max(0, Math.min(value, maxValue)) / maxValue) * height;
}

function svgEl(tagName, attrs = {}, text = null) {
  const element = document.createElementNS(SVG_NS, tagName);
  for (const [name, value] of Object.entries(attrs)) {
    element.setAttribute(name, String(value));
  }
  if (text !== null) {
    element.textContent = text;
  }
  return element;
}

function renderReasoning(events, frame = null) {
  if (!events.length) {
    renderEmptyPane(
      els.reasoningPane,
      frame?.isPrelude ? "No reasoning yet" : "No reasoning for this phase",
    );
    return;
  }
  els.reasoningPane.replaceChildren(...events.map(renderReasoningBlock));
}

function renderReasoningBlock(event) {
  const block = document.createElement("article");
  block.className = "detail-block";

  const title = document.createElement("h3");
  title.textContent = displayValue(event.power, "Power");

  const body = document.createElement("p");
  body.textContent = event.missing_reasoning
    ? "No reasoning recorded for this order response."
    : displayValue(event.reasoning || event.message, "");
  block.append(title, body);
  return block;
}

function renderEmptyPane(container, text) {
  const empty = document.createElement("p");
  empty.className = "empty-list";
  empty.textContent = text;
  container.replaceChildren(empty);
}

function setDetailTab(tabName) {
  state.activeDetailTab = tabName || "orders";
  els.detailTabs.forEach((tab) => {
    const isActive = tab.dataset.detailTab === state.activeDetailTab;
    tab.classList.toggle("active", isActive);
  });
  [els.ordersPane, els.reasoningPane, els.statsPane, els.infoPane, els.livePane].forEach((pane) => {
    pane.classList.remove("active");
  });
  const activePane = {
    orders: els.ordersPane,
    reasoning: els.reasoningPane,
    stats: els.statsPane,
    info: els.infoPane,
    live: els.livePane,
  }[state.activeDetailTab];
  (activePane || els.ordersPane).classList.add("active");
}

function togglePlayback() {
  if (state.timer) {
    stopPlayback();
    return;
  }
  if (!state.replay || state.frameIndex >= state.replay.frames.length - 1) {
    showFrame(0);
  }
  state.timer = window.setInterval(() => {
    showFrame(state.frameIndex + 1);
  }, 1200);
  els.playBtn.textContent = "\u275A\u275A";
  els.playBtn.title = "Pause";
  els.playBtn.setAttribute("aria-label", "Pause");
}

function stopPlayback() {
  if (state.timer) {
    window.clearInterval(state.timer);
    state.timer = null;
  }
  els.playBtn.textContent = "\u25B6";
  els.playBtn.title = "Play";
  els.playBtn.setAttribute("aria-label", "Play");
}

function setControlsEnabled(enabled) {
  els.prevBtn.disabled = !enabled;
  els.playBtn.disabled = !enabled;
  els.nextBtn.disabled = !enabled;
  els.phaseSlider.disabled = !enabled;
  updateTimelineState();
}

function updateTimelineState() {
  const hasReplay = Boolean(state.replay && state.replay.frames?.length);
  const maxIndex = hasReplay ? state.replay.frames.length - 1 : 0;
  const isBehindLive = state.liveRunActive && hasReplay && state.frameIndex < maxIndex;

  if (state.liveRunActive) {
    els.timelineState.textContent = isBehindLive ? "Past phase" : "Live tail";
  } else {
    els.timelineState.textContent = hasReplay ? "Replay" : "";
  }

  els.timelineState.classList.toggle("is-live", state.liveRunActive && !isBehindLive);
  els.timelineState.classList.toggle("is-past", isBehindLive);
  els.jumpLiveBtn.hidden = !isBehindLive;
  els.jumpLiveBtn.disabled = !isBehindLive;
}

function setLaunchEnabled(enabled) {
  const isReplay = els.runMode.value === "replay";
  const hasLaunchTarget = isReplay ? Boolean(state.runs.length) : Boolean(state.setups.length);
  els.launchBtn.disabled = !enabled || !hasLaunchTarget;
  els.setupSelect.disabled = !enabled || isReplay;
  els.launcherRunSelect.disabled = !enabled || !state.runs.length || !isReplay;
  els.runMode.disabled = !enabled;
  syncLaunchMode();
}

function updateLaunchModeTabs(mode) {
  els.modeTabs.forEach((tab) => {
    const isActive = tab.dataset.mode === mode;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.disabled = els.runMode.disabled;
  });
}

function syncLaunchMode() {
  const mode = els.runMode.value;
  const isLive = mode === "live";
  const isReplay = mode === "replay";
  updateLaunchModeTabs(mode);
  const selectedReplayRun = isReplay ? runById(els.launcherRunSelect.value) : null;
  const selectedReplayIsActive = isActiveRun(selectedReplayRun);
  const activeRunBlocksLaunch = Boolean(state.activeRun) && !isReplay;
  const liveCreditBlocksLaunch = isLive && liveProviderCreditBlocked();
  const hasLaunchTarget = isReplay ? Boolean(state.runs.length) : Boolean(state.setups.length);
  els.launchBtn.disabled = activeRunBlocksLaunch || liveCreditBlocksLaunch || !hasLaunchTarget || els.runMode.disabled;
  els.launchBtn.textContent = isReplay
    ? (selectedReplayIsActive ? "Open live run" : "Open replay")
    : "Launch";
  els.setupField.hidden = isReplay;
  els.setupSelect.disabled = isReplay || els.runMode.disabled || !state.setups.length || activeRunBlocksLaunch || liveCreditBlocksLaunch;
  els.launcherRunPicker.hidden = !isReplay;
  els.launcherRunSelect.disabled = !isReplay || !state.runs.length || els.runMode.disabled;
  els.apiKeyField.hidden = !isLive;
  els.apiKeyInput.disabled = !isLive || els.launchBtn.disabled;
  els.apiKeyInput.required = isLive;
  if (isReplay) {
    setLauncherStatus(state.runs.length ? "Choose a historic run" : "No historic runs found");
    if (
      (state.activeRun && els.launcherWarning.textContent === activeRunWarningText(state.activeRun))
      || isNoCreditReason(els.launcherWarning.textContent)
    ) {
      clearLauncherWarning();
    }
  } else if (activeRunBlocksLaunch) {
    setLauncherStatus("Demo run already in progress");
    setLauncherWarning(activeRunWarningText(state.activeRun));
  } else if (liveCreditBlocksLaunch) {
    setLauncherStatus("No OpenRouter credits available");
    setLauncherWarning(liveLaunchWarningText("openrouter_no_credits"));
  } else if (state.setups.length) {
    setLauncherStatus("Choose a setup to start");
  }
}

function setStatus(text) {
  els.statusText.textContent = text;
}

function setLauncherStatus(text) {
  els.launcherStatus.textContent = text;
}

function setLauncherWarning(text) {
  els.launcherWarning.textContent = text;
  els.launcherWarning.hidden = !text;
}

function clearLauncherWarning() {
  setLauncherWarning("");
}

function liveLaunchWarningText(reason) {
  const replayHint = "To browse existing runs without a working key, open the Replay tab.";
  if (reason === "missing_key") {
    return `OpenRouter API key required. ${replayHint}`;
  }
  const normalizedReason = String(reason || "").toLowerCase();
  if (normalizedReason.includes("already in progress")) {
    return activeRunWarningText(state.activeRun);
  }
  if (isNoCreditReason(normalizedReason)) {
    return noOpenRouterCreditsWarningText();
  }
  if (normalizedReason.includes("rejected") || normalizedReason.includes("401") || normalizedReason.includes("403")) {
    return `The OpenRouter API key is invalid or was rejected. Check that the key is correct and has access. ${replayHint}`;
  }
  return (
    "The live provider could not start the run. Check the OpenRouter key, credits, and connection. "
    + replayHint
  );
}

function liveProviderCreditBlocked() {
  return state.liveProvider?.status === "credit_exhausted";
}

function isNoCreditReason(reason) {
  const normalizedReason = String(reason || "").toLowerCase();
  return (
    normalizedReason.includes("openrouter_no_credits")
    || normalizedReason.includes("payment_required_or_insufficient_credits")
    || normalizedReason.includes("no openrouter credits")
    || normalizedReason.includes("insufficient openrouter credits")
    || normalizedReason.includes("credit_exhausted")
  );
}

function noOpenRouterCreditsWarningText() {
  const providerMessage = state.liveProvider?.message || "";
  if (providerMessage) {
    return providerMessage;
  }
  return "No OpenRouter credits are available. Live runs cannot continue or start right now. Open the Replay tab to inspect existing runs.";
}

function activeRunWarningText(run) {
  const label = run?.label || run?.run_id || "the active run";
  return (
    `A demo run is already in progress: ${label}. `
    + "Open the Replay tab and select the LIVE run to return to its live view, or wait for it to finish."
  );
}

function displayValue(value, fallback = "-") {
  return typeof value === "string" && value ? value : fallback;
}

boot().catch((error) => {
  stopPlayback();
  setStatus(error.message);
  setLauncherStatus(error.message);
  renderEmptyBoard("Replay unavailable");
  renderEmptyMessages("Replay unavailable");
  renderPhaseDetails([], []);
  renderLiveEvents();
  setControlsEnabled(false);
});
